"""
教学数仓数据生成器测试

用 small 规模验证：结果可复现、金额在订单头和明细之间严格对平、状态与支付时间一致、
退款不超过实付、所有外键都能关联上、行为数据满足“买过的人一定浏览过”
"""

from collections import defaultdict
from datetime import date

import pytest

from app.scripts.dw_generator import END_TIME, generate


@pytest.fixture(scope="module")
def data():
    return generate("small")


def test_generation_is_deterministic(data):
    again = generate("small")
    for table in data:
        assert data[table] == again[table], table


def test_order_amounts_reconcile_with_items(data):
    items_by_order = defaultdict(list)
    for item in data["fact_order_item"]:
        assert item["item_amount"] == item["unit_price"] * item["quantity"]
        assert item["pay_amount"] == item["item_amount"] - item["discount_amount"]
        assert item["pay_amount"] > 0
        items_by_order[item["order_id"]].append(item)
    for order in data["fact_order"]:
        items = items_by_order[order["order_id"]]
        assert items, order["order_id"]
        assert order["total_amount"] == sum(i["item_amount"] for i in items)
        assert order["discount_amount"] == sum(i["discount_amount"] for i in items)
        assert order["item_quantity"] == sum(i["quantity"] for i in items)
        assert (
            order["order_amount"]
            == order["total_amount"]
            - order["discount_amount"]
            + order["freight_amount"]
        )


def test_status_matches_payment(data):
    statuses = set()
    for order in data["fact_order"]:
        statuses.add(order["order_status"])
        paid = order["order_status"] in ("已支付", "已发货", "已完成")
        assert (order["pay_time"] is not None) == paid
        assert (order["pay_date_id"] is not None) == paid
        assert (order["payment_method"] is not None) == paid
        if paid:
            assert order["order_time"] <= order["pay_time"] <= END_TIME
    assert {"已取消", "已完成"} <= statuses


def test_refunds_are_valid(data):
    items = {i["order_item_id"]: i for i in data["fact_order_item"]}
    orders = {o["order_id"]: o for o in data["fact_order"]}
    assert data["fact_refund"]
    for refund in data["fact_refund"]:
        item = items[refund["order_item_id"]]
        order = orders[refund["order_id"]]
        assert item["order_id"] == refund["order_id"]
        assert (
            order["pay_time"] is not None and refund["apply_time"] > order["pay_time"]
        )
        assert 0 < refund["refund_quantity"] <= item["quantity"]
        assert 0 <= refund["refund_amount"] <= item["pay_amount"]
        assert (refund["finish_time"] is None) == (refund["refund_status"] == "退款中")


def test_foreign_keys_resolve(data):
    keys = {
        "dim_user": {r["user_id"] for r in data["dim_user"]},
        "dim_region": {r["region_id"] for r in data["dim_region"]},
        "dim_sku": {r["sku_id"] for r in data["dim_sku"]},
        "dim_shop": {r["shop_id"] for r in data["dim_shop"]},
        "dim_category": {r["category_id"] for r in data["dim_category"]},
        "dim_date": {r["date_id"] for r in data["dim_date"]},
        "dim_activity": {r["activity_id"] for r in data["dim_activity"]},
    }
    for o in data["fact_order"]:
        assert o["user_id"] in keys["dim_user"] and o["shop_id"] in keys["dim_shop"]
        assert o["receiver_region_id"] in keys["dim_region"]
        assert o["order_date_id"] in keys["dim_date"]
        assert o["pay_date_id"] is None or o["pay_date_id"] in keys["dim_date"]
    for i in data["fact_order_item"]:
        assert i["sku_id"] in keys["dim_sku"]
        assert i["activity_id"] is None or i["activity_id"] in keys["dim_activity"]
    for s in data["dim_sku"]:
        assert (
            s["category_id"] in keys["dim_category"]
            and s["shop_id"] in keys["dim_shop"]
        )
    for b in data["fact_user_behavior"]:
        assert b["user_id"] in keys["dim_user"] and b["sku_id"] in keys["dim_sku"]
        assert b["date_id"] in keys["dim_date"]


def test_buyers_browsed_before_ordering(data):
    first_browse = {}
    for b in data["fact_user_behavior"]:
        if b["behavior_type"] == "浏览":
            key = (b["user_id"], b["sku_id"])
            first_browse[key] = min(
                first_browse.get(key, b["behavior_time"]), b["behavior_time"]
            )
    orders = {o["order_id"]: o for o in data["fact_order"]}
    for item in data["fact_order_item"]:
        order = orders[item["order_id"]]
        assert first_browse[(order["user_id"], item["sku_id"])] <= order["order_time"]


def test_dates_within_range(data):
    assert data["dim_date"][0]["full_date"] == date(2024, 1, 1)
    assert data["dim_date"][-1]["full_date"] == date(2025, 12, 31)
    users = {u["user_id"]: u for u in data["dim_user"]}
    for o in data["fact_order"]:
        assert o["order_time"].date() >= users[o["user_id"]]["register_date"]
