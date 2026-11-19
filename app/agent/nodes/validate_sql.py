"""
SQL 校验节点

负责在真正执行查询前检查生成的 SQL：先做程序侧只读检查，再交给数据库 EXPLAIN 解析，
最后按外键 ref 关系做语义检查（扇出聚合、关联条件不符合 ref），发现问题同样交给 correct_sql 修正
校验结果不在这里决定流程走向，而是通过 state["error"] 交给 graph.py 的条件边判断：
通过则执行，不通过且修正次数未用完则进入 correct_sql，修正后再回到这里重新校验
修正次数用完仍未通过时，本节点直接抛出异常结束本次查询
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.sql_lint import ForeignKey, lint_sql
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository


class SQLValidationError(RuntimeError):
    """SQL 修正次数用完后仍无法通过校验"""


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """校验 SQL，并返回 error 字段控制后续条件分支"""

    writer = runtime.stream_writer
    step = "校验SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    # 读取 generate_sql 或 correct_sql 写入状态的候选 SQL
    sql = state["sql"]
    # SQL 可用性必须交给真实数仓判断，这里从运行时上下文取 DW Repository
    dw_mysql_repository: DWMySQLRepository = runtime.context["dw_mysql_repository"]

    try:
        # validate 内部先做只读检查，再 explain <sql>
        await dw_mysql_repository.validate(sql)
    except Exception as e:
        # SQLAlchemy 的异常文本会附带整段 SQL 和文档链接，只保留数据库原始报错，
        # 让修正提示词和前端展示都更聚焦
        error = str(getattr(e, "orig", None) or e)
        attempts = state.get("correct_attempts", 0)
        max_attempts = app_config.agent.max_correct_attempts
        logger.info(f"SQL校验未通过（已修正 {attempts} 次）：{error}")
        # 校验未通过用 error 状态展示，detail 携带数据库返回的具体原因
        writer({"type": "progress", "step": step, "status": "error", "detail": error})
        if attempts >= max_attempts:
            raise SQLValidationError(
                f"SQL 经过 {attempts} 次修正仍未通过校验：{error}"
            ) from e
        # 不中断图执行，把错误写入状态，由条件边进入 correct_sql
        return {"error": error}

    # 能执行不代表业务上正确：再用外键 ref 关系做语义检查（扇出聚合、关联条件不符合 ref）
    issues = await _semantic_issues(sql, runtime)
    if issues:
        attempts = state.get("correct_attempts", 0)
        detail = "SQL 可以执行，但存在业务逻辑问题：\n" + "\n".join(
            f"- {issue}" for issue in issues
        )
        logger.info(f"SQL语义检查发现问题（已修正 {attempts} 次）：{issues}")
        if attempts < app_config.agent.max_correct_attempts:
            writer(
                {"type": "progress", "step": step, "status": "error", "detail": detail}
            )
            return {"error": detail, "lint_issues": issues}
        # 修正次数用完：语义检查只是提示，可能误报，不阻断执行
        logger.warning(f"SQL语义问题在修正后仍存在，继续执行：{issues}")
        writer(
            {"type": "progress", "step": step, "status": "success", "detail": detail}
        )
        return {"error": None, "lint_issues": issues}

    logger.info("SQL校验通过")
    writer({"type": "progress", "step": step, "status": "success"})
    return {"error": None, "lint_issues": []}


async def _semantic_issues(sql: str, runtime: Runtime[DataAgentContext]) -> list[str]:
    """按配置对 SQL 做语义检查；外键关系从元数据库读取，检查本身出错时不影响主流程"""
    if not app_config.agent.semantic_check:
        return []
    try:
        meta_mysql_repository = runtime.context["meta_mysql_repository"]
        fks = [
            ForeignKey(fk.table_id, fk.name, *fk.ref.split(".", 1))
            for fk in await meta_mysql_repository.get_foreign_key_columns()
        ]
        return lint_sql(sql, fks)
    except Exception as e:
        logger.warning(f"SQL语义检查失败，跳过：{e}")
        return []
