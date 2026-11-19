"""
已验证问答示例实体

人工确认过的“问题 - SQL”对，问数时召回相似问题作为 few-shot 示例
（参考 Vanna、Databricks Genie 的 example SQL、Snowflake Cortex Analyst 的 verified queries）
"""

from dataclasses import dataclass, field


@dataclass
class SqlExample:
    """一条已验证的问答示例"""

    id: str
    question: str
    sql: str
    # 示例 SQL 用到的表，召回后并入候选表，帮助补全上下文
    tables: list[str] = field(default_factory=list)
