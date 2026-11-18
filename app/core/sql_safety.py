"""
SQL 安全工具

大模型生成的 SQL 在交给数仓之前，需要在程序侧做两件事：
1. 清洗：去掉模型偶尔输出的 Markdown 代码块标记和多余的分号空白
2. 只读校验：用 sqlglot 解析 SQL，确保只有一条查询语句，且不包含任何写入、
   DDL、加锁或导出操作。不能只靠 Prompt 约束模型“只生成查询”，
   因为 MySQL 的 DDL 会隐式提交，一旦执行就无法回滚
"""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# 出现在语法树任意位置都要拒绝的节点类型：写操作、DDL、加锁、SELECT INTO、原样透传的命令
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Lock,
    exp.Into,
    exp.Set,
)

# 匹配 ```sql ... ``` 或 ``` ... ``` 代码块，取出其中的 SQL 正文
_CODE_FENCE = re.compile(r"```(?:sql|mysql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class UnsafeSQLError(ValueError):
    """SQL 无法解析或不是只读查询时抛出，错误信息会交给 SQL 修正节点参考"""


def clean_sql(text: str) -> str:
    """去掉代码块标记、首尾空白和末尾分号，得到可直接执行的 SQL 文本"""

    match = _CODE_FENCE.search(text)
    if match:
        text = match.group(1)
    return text.strip().rstrip(";").strip()


def ensure_readonly(sql: str, dialect: str = "mysql") -> None:
    """校验 SQL 是单条只读查询，不满足时抛出 UnsafeSQLError"""

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        raise UnsafeSQLError(f"SQL 无法解析：{e}") from e

    if len(statements) != 1:
        raise UnsafeSQLError(f"只允许执行一条 SQL，实际解析出 {len(statements)} 条")

    statement = statements[0]
    # Query 覆盖 SELECT、WITH ... SELECT、UNION 等所有查询形态
    if not isinstance(statement, exp.Query):
        raise UnsafeSQLError(
            f"只允许执行查询语句，实际语句类型为 {statement.key.upper()}"
        )

    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSQLError(f"查询中包含不允许的操作：{node.key.upper()}")
