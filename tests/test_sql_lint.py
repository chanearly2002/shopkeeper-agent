"""
SQL 语义检查测试

外键关系直接取自 conf/meta_config.yaml；用评测集验证：50 条标准 SQL 都不应被报，
扇出类陷阱的错误写法应被报出
"""

import pytest
import yaml

from app.core.sql_lint import foreign_keys_from_refs, lint_sql
from evals.dataset import DEFAULT_DATASET, ROOT, load_dataset


@pytest.fixture(scope="module")
def fks():
    config = yaml.safe_load(
        (ROOT / "conf" / "meta_config.yaml").read_text(encoding="utf-8")
    )
    refs = {
        f"{t['name']}.{c['name']}": c["ref"]
        for t in config["tables"]
        for c in t["columns"]
        if c.get("ref")
    }
    return foreign_keys_from_refs(refs)


def test_no_false_positive_on_gold_sql(fks):
    for item in load_dataset(DEFAULT_DATASET):
        for turn in item["turns"]:
            assert lint_sql(turn["gold_sql"], fks) == [], item["id"]


@pytest.mark.parametrize("qid", ["fanout_01", "fanout_02", "fanout_03", "fanout_04"])
def test_fanout_traps_detected(fks, qid):
    item = next(i for i in load_dataset(DEFAULT_DATASET) if i["id"] == qid)
    issues = lint_sql(item["turns"][0]["trap_sql"], fks)
    assert any("重复计算" in i and "fact_order_item" in i for i in issues)


def test_row_level_expression_is_not_fanout(fks):
    sql = """select sum(i.pay_amount - k.cost_price * i.quantity)
             from fact_order_item i join dim_sku k on i.sku_id = k.sku_id"""
    assert lint_sql(sql, fks) == []


def test_exists_subquery_is_not_fanout(fks):
    sql = """select sum(o.order_amount) from fact_order o
             where exists (select 1 from fact_order_item i where i.order_id = o.order_id)"""
    assert lint_sql(sql, fks) == []


def test_wrong_join_column_detected(fks):
    sql = """select count(*) from fact_order o
             join dim_region r on o.user_id = r.region_id"""
    assert any("不符合外键 ref" in i for i in lint_sql(sql, fks))


def test_role_playing_join_follows_ref(fks):
    sql = """select count(*) from fact_order o
             join dim_region r on o.receiver_region_id = r.region_id
             join dim_date d on o.pay_date_id = d.date_id"""
    assert lint_sql(sql, fks) == []


def test_sibling_foreign_keys_allowed(fks):
    # 退款和订单明细都通过 order_id 指向订单
    sql = """select count(*) from fact_refund r
             join fact_order_item i on r.order_id = i.order_id"""
    assert not any("不符合外键 ref" in i for i in lint_sql(sql, fks))


def test_unparseable_sql_is_ignored(fks):
    assert lint_sql("这不是 SQL", fks) == []
