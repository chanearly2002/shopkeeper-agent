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


def test_year_month_labels_match_month_numbers():
    gold = [{"月份": 1, "销售额": 10.5}, {"月份": 2, "销售额": 20.0}]
    for labels in (
        ["2025-01", "2025-02"],
        [202501, 202502],
        ["202501", "202502"],
        ["2025年1月", "2025年2月"],
    ):
        pred = [{"m": labels[0], "amt": 10.5}, {"m": labels[1], "amt": 20.0}]
        assert results_equal(pred, gold), labels


def test_year_month_requires_single_year_and_aligned_rows():
    gold = [{"月份": 1, "销售额": 10.5}, {"月份": 2, "销售额": 20.0}]
    # 跨年的月份标签不换算：“1 月”指哪年说不清
    assert not results_equal(
        [{"m": "2024-01", "amt": 10.5}, {"m": "2025-02", "amt": 20.0}], gold
    )
    # 月份换算后仍要逐行对齐：金额挂错月份要判错
    assert not results_equal(
        [{"m": "2025-01", "amt": 20.0}, {"m": "2025-02", "amt": 10.5}], gold
    )
    # 月份值本身错了
    assert not results_equal(
        [{"m": "2025-03", "amt": 10.5}, {"m": "2025-02", "amt": 20.0}], gold
    )


def test_dates_match_date_keys():
    gold = [{"日期": 20251225, "销售额": 1.0}, {"日期": 20251226, "销售额": 2.0}]
    pred = [{"d": date(2025, 12, 25), "v": 1.0}, {"d": "2025-12-26", "v": 2.0}]
    assert results_equal(pred, gold)
    assert results_equal(
        gold, [{"d": "2025-12-25", "v": 1.0}, {"d": "2025-12-26", "v": 2.0}]
    )
    assert not results_equal(
        [{"d": "2025-12-24", "v": 1.0}, {"d": "2025-12-26", "v": 2.0}], gold
    )
