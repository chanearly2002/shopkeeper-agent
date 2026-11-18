"""关联路径补全测试：用新数仓的外键关系 (子表, 父表) 验证能补出正确的中间表"""

from app.agent.join_path import find_bridge_tables

EDGES = [
    ("dim_user", "dim_region"),
    ("dim_sku", "dim_category"),
    ("dim_sku", "dim_shop"),
    ("fact_order", "dim_user"),
    ("fact_order", "dim_shop"),
    ("fact_order", "dim_region"),
    ("fact_order", "dim_date"),
    ("fact_order_item", "fact_order"),
    ("fact_order_item", "dim_sku"),
    ("fact_order_item", "dim_activity"),
    ("fact_refund", "fact_order_item"),
    ("fact_refund", "fact_order"),
    ("fact_refund", "dim_user"),
    ("fact_refund", "dim_date"),
    ("fact_user_behavior", "dim_user"),
    ("fact_user_behavior", "dim_sku"),
    ("fact_user_behavior", "dim_date"),
]

FACTS = {"fact_order", "fact_order_item", "fact_refund", "fact_user_behavior"}


def bridges(candidates):
    return find_bridge_tables(candidates, EDGES, FACTS)


def test_no_bridge_needed_for_directly_joined_tables():
    assert bridges(["fact_order", "dim_region", "dim_date"]) == []


def test_order_to_category_goes_through_item_and_sku():
    # 同样是三跳，经由店铺展开到店铺全部商品会扇出，必须经由订单明细
    assert bridges(["fact_order", "dim_date", "dim_category"]) == [
        "fact_order_item",
        "dim_sku",
    ]


def test_order_to_activity_goes_through_item():
    assert bridges(["fact_order", "dim_activity"]) == ["fact_order_item"]


def test_refund_to_category():
    assert bridges(["fact_refund", "dim_category"]) == ["fact_order_item", "dim_sku"]


def test_single_table_and_unknown_tables():
    assert bridges(["fact_order"]) == []
    assert bridges(["fact_order", "not_in_graph"]) == []


def test_result_is_stable():
    first = bridges(["fact_refund", "dim_category", "dim_region"])
    assert first == bridges(["fact_refund", "dim_category", "dim_region"])
    assert "dim_sku" in first and "dim_shop" not in first
