"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支

多轮问数在单轮流水线外面再套一层会话图（app/agent/session_graph.py），
它的状态 SessionState 按 thread_id 持久化，只保存每轮的问题、改写、SQL 和结果预览；
单轮的 DataAgentState 仍是一次性的，不进入检查点
"""

import operator
from typing import Annotated, NotRequired, TypedDict

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
    # 多轮追问时上一轮的问题和 SQL（已格式化），作为生成 SQL 的参考；单轮查询没有
    reference: NotRequired[str]
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


class ResultPreview(TypedDict):
    """一轮查询结果的预览，供下一轮改写引用，控制 token 只保留前几行"""

    columns: list[str]
    row_count: int
    truncated: bool
    rows: list[dict]


class Turn(TypedDict):
    """会话中已完成的一轮问答"""

    question: str  # 用户原话
    standalone: str  # 改写后的独立问题，实际交给单轮流水线的问题
    intent: str  # new / followup / result_qa，见 app/core/conversation.py
    sql: str | None  # 最终执行的 SQL，失败或直接基于结果回答时为 None
    result_preview: ResultPreview | None  # 结果预览，失败或直接回答时为 None
    error: str | None  # 本轮失败原因
    answer: NotRequired[str | None]  # 基于上一轮结果直接回答的文本
    clarification: NotRequired[str | None]  # 本轮反问后用户的回答
    today: str  # 本轮使用的“今天”


class SessionState(TypedDict):
    """多轮会话状态，由外层会话图的 checkpointer 按 thread_id 持久化"""

    user_input: str  # 本轮用户输入
    # 会话的“今天”：首轮确定后整段会话沿用，请求里显式传入时覆盖
    today: NotRequired[str]
    # 历史轮次只追加不修改；reducer 让 remember 节点每轮只需返回新的一轮
    history: Annotated[list[Turn], operator.add]

    # 最近一次查库的完整结果（不超过 conversation.answer_max_rows 行，值已转成基础类型），
    # 供下一轮“哪个最高”这类提问直接回答；只有 run_pipeline 会更新它，直接回答的轮次不改
    last_result: NotRequired[list[dict] | None]

    # 以下是“本轮”的数据，每轮由 contextualize 先清空，不会串到下一轮
    intent: str
    standalone: str
    sql: str | None
    result_preview: ResultPreview | None
    error: str | None
    answer: str | None  # answer_from_result 的回答，None 表示没有直接回答（要查库）
    clarify_question: str | None  # 需要反问时的问题
    clarify_options: list[str]  # 反问的候选项，可为空
    clarification: str | None  # 用户对反问的回答
    clarified: bool  # 本轮是否已经反问过；反问过就不再反问，避免循环
