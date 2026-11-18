"""
SQL 执行节点

负责执行已经通过校验的最终 SQL，并把结果写回状态和流式输出
它是当前 SQL 闭环的结束节点，执行完成后流程进入 END
结果写回 state 后，后续可以在它之后继续追加结果总结 图表推荐等节点
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger


async def run_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """执行 SQL 并产出最终问数结果"""

    writer = runtime.stream_writer
    step = "执行SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这里拿到的 SQL 已经通过 validate_sql 校验，可能来自 generate_sql 或 correct_sql
        sql = state["sql"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        # 真实数据库访问统一封装在仓储层，行数上限和执行超时都来自配置
        max_rows = app_config.agent.max_result_rows
        rows, truncated = await dw_mysql_repository.run(
            sql,
            max_rows=max_rows,
            max_execution_ms=app_config.agent.max_execution_ms,
        )
        logger.info(f"SQL执行完成，返回 {len(rows)} 行，截断：{truncated}")
        writer({"type": "progress", "step": step, "status": "success"})
        writer(
            {
                "type": "result",
                "data": rows,
                "sql": sql,
                "truncated": truncated,
                "max_rows": max_rows,
            }
        )
        return {"result": rows, "truncated": truncated}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
