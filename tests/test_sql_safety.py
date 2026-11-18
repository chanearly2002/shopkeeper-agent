"""
SQL 安全工具测试

覆盖模型输出清洗和只读校验：合法查询放行，写操作 DDL 多语句 加锁等一律拒绝
"""

import pytest

from app.core.sql_safety import UnsafeSQLError, clean_sql, ensure_readonly


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("select 1", "select 1"),
        ("  select 1;  ", "select 1"),
        ("```sql\nselect 1;\n```", "select 1"),
        ("```\nSELECT a FROM t\n```", "SELECT a FROM t"),
        ("好的，SQL 如下：\n```sql\nselect 1\n```\n以上", "select 1"),
    ],
)
def test_clean_sql(raw: str, expected: str):
    assert clean_sql(raw) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "select sum(order_amount) as gmv from fact_order",
        "with t as (select 1 as a) select * from t",
        "select 1 union all select 2",
        "(select 1) union (select 2) order by 1",
        "select * from dim_region where region_name = '华北;drop table x'",
        """
        select r.region_name, sum(f.order_amount) as gmv
        from fact_order f join dim_region r on f.region_id = r.region_id
        group by r.region_name order by gmv desc limit 5
        """,
    ],
)
def test_readonly_queries_pass(sql: str):
    ensure_readonly(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "drop table fact_order",
        "delete from fact_order",
        "update fact_order set order_amount = 0",
        "insert into fact_order select * from fact_order",
        "truncate table fact_order",
        "alter table fact_order add column x int",
        "create table t as select 1",
        "select 1; drop table fact_order",
        "select * from fact_order for update",
        "select * from fact_order into outfile '/tmp/x'",
        "show tables",
        "set @a = 1",
        "这不是 SQL",
        "",
    ],
)
def test_unsafe_sql_rejected(sql: str):
    with pytest.raises(UnsafeSQLError):
        ensure_readonly(sql)
