"""
数仓 MySQL 仓储

这一层对应文档里的 DW Repository，职责是到真实数仓中补齐配置文件里
没有显式维护的信息，例如字段类型和字段示例值。Service 层只关心
“需要哪些信息”，具体怎样查数仓由仓储层统一封装
SQL 生成闭环中的数据库环境读取 SQL 校验和最终查询执行也集中放在这里
校验和执行前都会先做程序侧的只读检查，不依赖 Prompt 约束模型“只生成查询”
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_safety import ensure_readonly


class DWMySQLRepository:
    """负责查询数仓真实表结构和字段样例值"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """查询整张表的字段类型，作为 ColumnInfo.type 的真实来源"""
        sql = f"show columns from {table_name}"
        result = await self.session.execute(text(sql))
        result_dict = result.mappings().fetchall()
        return {row["Field"]: row["Type"] for row in result_dict}

    async def get_column_values(
        self, table_name: str, column_name: str, limit: int = 10
    ) -> list:
        """抽样查询字段示例值，供元数据入库和后续检索链路复用"""
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        result = await self.session.execute(text(sql))
        return [row[0] for row in result.fetchall()]

    async def get_db_info(self):
        """读取当前数仓数据库的方言和版本，供 SQL 生成提示词使用"""

        sql = "select version()"
        result = await self.session.execute(text(sql))
        version = result.scalar()

        # dialect 来自 SQLAlchemy 当前绑定的数据库方言，例如 mysql
        dialect = self.session.bind.dialect.name
        return {"dialect": dialect, "version": version}

    async def validate(self, sql: str):
        """先做只读检查，再用 EXPLAIN 让数据库解析 SQL，发现语法 表名 字段名等错误"""
        ensure_readonly(sql, self.session.bind.dialect.name)
        await self.session.execute(text(f"explain {sql}"))

    async def run(
        self, sql: str, max_rows: int, max_execution_ms: int = 0
    ) -> tuple[list[dict], bool]:
        """
        执行最终 SQL，返回 (结果行, 是否被截断)

        结果行转换成前端更易消费的字典列表，最多返回 max_rows 行
        max_execution_ms 通过 MySQL 的 max_execution_time 限制单条查询耗时
        """
        ensure_readonly(sql, self.session.bind.dialect.name)
        if max_execution_ms > 0:
            await self.session.execute(
                text(f"set session max_execution_time = {int(max_execution_ms)}")
            )
        result = await self.session.execute(text(sql))
        # 多取一行用来判断是否超出上限
        rows = result.mappings().fetchmany(max_rows + 1)
        result.close()
        truncated = len(rows) > max_rows
        return [dict(row) for row in rows[:max_rows]], truncated
