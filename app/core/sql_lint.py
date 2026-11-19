"""
SQL 语义检查

EXPLAIN 只能保证 SQL 能执行，保证不了业务上正确。本模块利用元数据里外键的 ref 关系，
在程序侧检查两类常见的“能执行但算错”的写法：

1. 扇出聚合：对表 T 的字段做 SUM / AVG，同一层查询里又关联了引用 T 的子表 C（C 的外键 ref 指向 T），
   T 的每一行会按 C 的行数被复制，金额被重复计算。例如订单金额关联订单明细后求和
2. 关联条件不符合 ref：两张物理表的等值 JOIN 条件既不是 ref 声明的外键关系，
   也不是两个外键指向同一个目标字段，大概率连错了列

只检查同一层 SELECT 内直接出现的物理表；子查询、派生表会被当作独立的一层分别检查，
EXISTS / IN 子查询这种不会扇出的写法因此不会被误报

本模块是纯函数，不依赖数据库和项目配置，方便单元测试
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class ForeignKey:
    """外键关系：child_table.child_column 引用 parent_table.parent_column"""

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


def foreign_keys_from_refs(refs: dict[str, str]) -> list[ForeignKey]:
    """把 {"表.外键字段": "目标表.目标字段"} 转换成 ForeignKey 列表"""
    result = []
    for column_id, ref in refs.items():
        child_table, child_column = column_id.split(".", 1)
        parent_table, parent_column = ref.split(".", 1)
        result.append(
            ForeignKey(child_table, child_column, parent_table, parent_column)
        )
    return result


def _scope_tables(select: exp.Select) -> dict[str, str]:
    """当前这一层 SELECT 的 FROM / JOIN 里直接出现的物理表：{别名或表名: 表名}"""
    sources = []
    # sqlglot 新版本把 FROM 存在 from_ 键下，旧版本是 from，两者都兼容
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause:
        sources.append(from_clause.this)
    sources += [join.this for join in select.args.get("joins") or []]
    tables = {}
    for source in sources:
        if isinstance(source, exp.Table):
            tables[source.alias_or_name] = source.name
    return tables


def _owner(node: exp.Expression, select: exp.Select) -> bool:
    """节点是否直接属于这一层 SELECT，而不是更内层的子查询"""
    return node.find_ancestor(exp.Select) is select


def _column_table(column: exp.Column, tables: dict[str, str]) -> str | None:
    """解析字段所属的物理表；没写表前缀时，只有一张表才能确定"""
    if column.table:
        return tables.get(column.table)
    if len(set(tables.values())) == 1:
        return next(iter(tables.values()))
    return None


def _check_fanout(
    select: exp.Select, tables: dict[str, str], fks: list[ForeignKey]
) -> list[str]:
    """
    聚合表达式只用到父表 T 的字段、而同一层又关联了引用 T 的子表时，判定为扇出

    表达式里只要出现了子表的字段（如 SUM(i.pay_amount - k.cost_price * i.quantity)），
    就是按子表粒度逐行计算，不会重复计算
    """
    issues = []
    present = set(tables.values())
    for agg in select.find_all(exp.Sum, exp.Avg):
        if not _owner(agg, select):
            continue
        func = "SUM" if isinstance(agg, exp.Sum) else "AVG"
        columns = list(agg.find_all(exp.Column))
        used = {t for t in (_column_table(c, tables) for c in columns) if t}
        for table in sorted(used):
            children = sorted(
                {
                    fk.child_table
                    for fk in fks
                    if fk.parent_table == table
                    and fk.child_table in present
                    and fk.child_table != table
                }
            )
            if children and not (used & set(children)):
                issues.append(
                    f"{func}({agg.this.sql()}) 聚合的是 {table} 的字段，但同一层查询还关联了引用它的 "
                    f"{'、'.join(children)}，{table} 的每一行会被重复计算。"
                    f"应改用 {children[0]} 上对应粒度的字段，或先在子查询里按 {table} 聚合、"
                    f"用 EXISTS / IN 判断后再关联"
                )
    return list(dict.fromkeys(issues))


def _check_join_conditions(
    select: exp.Select, tables: dict[str, str], fks: list[ForeignKey]
) -> list[str]:
    issues = []
    ref_pairs = {
        (fk.child_table, fk.child_column, fk.parent_table, fk.parent_column)
        for fk in fks
    }
    target_of = {
        (fk.child_table, fk.child_column): (fk.parent_table, fk.parent_column)
        for fk in fks
    }
    pk_columns = {(fk.parent_table, fk.parent_column) for fk in fks}

    for join in select.args.get("joins") or []:
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            lt, rt = tables.get(left.table), tables.get(right.table)
            if lt is None or rt is None or lt == rt:
                continue
            a, b = (lt, left.name), (rt, right.name)
            if (*a, *b) in ref_pairs or (*b, *a) in ref_pairs:
                continue
            # 两个外键指向同一个目标（如退款明细与订单明细都通过 order_id 指向订单），允许
            ta, tb = (
                target_of.get(a, a if a in pk_columns else None),
                target_of.get(b, b if b in pk_columns else None),
            )
            if ta is not None and ta == tb:
                continue
            issues.append(
                f"关联条件 {eq.sql()} 不符合外键 ref 关系，请检查是否连错了字段"
                f"（{lt}.{left.name} 与 {rt}.{right.name} 之间没有声明关联）"
            )
    return list(dict.fromkeys(issues))


def lint_sql(sql: str, fks: list[ForeignKey], dialect: str = "mysql") -> list[str]:
    """返回 SQL 中的业务逻辑问题列表，为空表示没有发现问题；解析失败时不报（交给只读校验处理）"""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return []
    issues: list[str] = []
    for select in tree.find_all(exp.Select):
        tables = _scope_tables(select)
        if not tables:
            continue
        issues += _check_fanout(select, tables, fks)
        issues += _check_join_conditions(select, tables, fks)
    return list(dict.fromkeys(issues))
