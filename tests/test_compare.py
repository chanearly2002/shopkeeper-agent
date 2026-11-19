"""评测结果判定测试：判定器出错会让整套评测失去意义，所以每条规则都要有用例"""

from datetime import date
from decimal import Decimal

from evals.compare import results_equal, values_equal

GOLD = [
    {"area": "华东", "支付金额": Decimal("3137307.44")},
    {"area": "华南", "支付金额": Decimal("1967840.95")},
    {"area": "华北", "支付金额": Decimal("1199174.27")},
]


def test_identical_results():
    assert results_equal(GOLD, GOLD)


def test_column_names_and_order_ignored():
    pred = [{"销售额": r["支付金额"], "大区": r["area"]} for r in GOLD]
    assert results_equal(pred, GOLD)


def test_extra_columns_allowed():
    pred = [{**r, "订单数": i} for i, r in enumerate(GOLD)]
    assert results_equal(pred, GOLD)


def test_missing_column_fails():
    pred = [{"area": r["area"]} for r in GOLD]
    assert not results_equal(pred, GOLD)


def test_row_order_only_when_required():
    reversed_rows = list(reversed(GOLD))
    assert results_equal(reversed_rows, GOLD, order_matters=False)
    assert not results_equal(reversed_rows, GOLD, order_matters=True)


def test_rows_must_stay_aligned():
    # 每列单独看都对，但金额和大区错位了
    pred = [
        {"area": a["area"], "支付金额": b["支付金额"]}
        for a, b in zip(GOLD, GOLD[1:] + GOLD[:1])
    ]
    assert not results_equal(pred, GOLD)


def test_numeric_tolerance_and_rounding():
    assert values_equal(3137307.44, 3137307.4400001)
    assert values_equal(0.12, 0.1234)  # 预测值保留两位小数
    assert values_equal(3137307.0, 3137307.44)  # 大金额取整
    assert not values_equal(0.0, 0.1234)  # 小数值不能靠取整蒙混
    assert not values_equal(3137307.44, 3137306.44)


def test_percent_scale_allowed():
    gold = [{"退款率": Decimal("0.0461")}]
    assert results_equal([{"退款率": 4.61}], gold)
    assert not results_equal([{"退款率": 46.1}], gold)


def test_types_normalized():
    gold = [{"d": date(2025, 6, 18), "n": 3}]
    assert results_equal([{"d": "2025-06-18", "n": Decimal("3")}], gold)


def test_different_row_counts_fail():
    assert not results_equal(GOLD[:2], GOLD)
    assert results_equal([], [])
    assert not results_equal([], GOLD)


def test_null_handling():
    assert results_equal([{"x": None}], [{"x": None}])
    assert not results_equal([{"x": 0}], [{"x": None}])
