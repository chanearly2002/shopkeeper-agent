"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支
"""

from typing import NotRequired, TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.sql_example import SqlExample
from app.entities.term_info import TermInfo
from app.entities.value_info import ValueInfo


class MetricInfoState(TypedDict):
    """面向 SQL 生成提示词的指标信息"""

    name: str
    description: str
    # 指标依赖的字段 id，用来提示模型不要脱离业务口径随意计算
    relevant_columns: list[str]
    alias: list[str]


class ColumnInfoState(TypedDict):
    """表上下文中的字段信息"""

    name: str
    type: str
    role: str
    # 字段真实样例值，尤其用于辅助 where 条件里的枚举值选择
    examples: list
    description: str
    alias: list[str]
    # 只有外键才有：关联的目标字段 表名.字段名，SQL 的 JOIN 条件必须按它来写
    ref: NotRequired[str]


class TableInfoState(TypedDict):
    """SQL 生成阶段真正传给模型的表结构上下文"""

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class DateInfoState(TypedDict):
    """SQL 生成阶段使用的当前日期上下文"""

    date: str
    weekday: str
    quarter: int


class DBInfoState(TypedDict):
    """SQL 生成阶段使用的数据库环境信息"""

    dialect: str
    version: str


class DataAgentState(TypedDict):
    """一次问数链路中的核心状态"""

    query: str  # 用户输入的查询
    # 本次查询的“今天”（YYYY-MM-DD），不传则用 agent.today 配置；评测每题、多轮每个会话都可以单独指定
    today: NotRequired[str]
    keywords: list[str]  # 抽取的关键词
    retrieved_column_infos: list[ColumnInfo]  # 检索到的字段信息
    retrieved_metric_infos: list[MetricInfo]  # 检索到的指标信息
    retrieved_value_infos: list[ValueInfo]  # 检索到的取值信息
    term_infos: list[TermInfo]  # 命中的业务术语
    example_infos: list[SqlExample]  # 召回的已验证问答示例

    table_infos: list[TableInfoState]  # 合并和补齐后的表结构上下文
    metric_infos: list[MetricInfoState]  # 合并后的指标上下文
    date_info: DateInfoState  # 当前日期 星期和季度信息
    db_info: DBInfoState  # 数据库方言和版本信息

    sql: str  # 生成或校正后的SQL

    error: str | None  # 校验SQL时出现的错误信息，None 表示校验通过
    correct_attempts: int  # 已经执行过的 SQL 修正次数，用来限制校验-修正循环
    lint_issues: list[str]  # 最近一次语义检查发现的问题，修正次数用完仍存在时会保留下来

    result: list[dict]  # 最终查询结果，最多 agent.max_result_rows 行
    truncated: bool  # 结果是否因超过行数上限被截断
