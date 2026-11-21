"""
多轮问数纯函数测试

覆盖结果预览、历史格式化和改写输出兜底；不依赖配置和外部服务
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
import yaml

from app.core.conversation import (
    INTENT_FOLLOWUP,
    INTENT_NEW,
    INTENT_RESULT_QA,
    format_history,
    parse_rewrite,
    summarize_result,
    to_plain,
)


def test_to_plain_converts_db_types():
    assert to_plain(Decimal("12.50")) == 12.5
    assert to_plain(Decimal("100.00")) == 100
    assert isinstance(to_plain(Decimal("100.00")), int)
    assert to_plain(date(2025, 1, 2)) == "2025-01-02"
    assert to_plain(datetime(2025, 1, 2, 3, 4, 5)) == "2025-01-02T03:04:05"
    assert to_plain(None) is None
    assert to_plain("华东") == "华东"


def test_summarize_result_keeps_head_rows_and_counts():
    rows = [{"area": f"区{i}", "amount": Decimal(i)} for i in range(8)]
    preview = summarize_result(rows, truncated=False, max_rows=3)
    assert preview["columns"] == ["area", "amount"]
    assert preview["row_count"] == 8
    assert preview["truncated"] is False
    assert preview["rows"] == [{"area": f"区{i}", "amount": i} for i in range(3)]


def test_summarize_empty_result():
    assert summarize_result([], truncated=False) == {
        "columns": [],
        "row_count": 0,
        "truncated": False,
        "rows": [],
    }


def _turn(i: int, **kw) -> dict:
    turn = {
        "question": f"原话{i}",
        "standalone": f"问题{i}",
        "intent": INTENT_NEW,
        "sql": f"select {i}",
        "result_preview": summarize_result([{"x": i}], False),
        "error": None,
        "today": "2025-12-31",
    }
    turn.update(kw)
    return turn


def test_format_history_keeps_recent_turns_with_numbers():
    history = [_turn(i) for i in range(1, 6)]
    items = yaml.safe_load(format_history(history, max_turns=3))
    # 只保留最近 3 轮，轮次编号保持在整段会话中的位置
    assert [item["轮次"] for item in items] == [3, 4, 5]
    assert items[-1]["问题"] == "问题5"
    assert items[-1]["SQL"] == "select 5"
    assert items[-1]["结果"] == {"列": ["x"], "行数": 1, "前几行": [{"x": 5}]}


def test_format_history_renders_multiline_sql_as_block():
    sql = "select a  \nfrom t\nwhere x = 1"
    text = format_history([_turn(1, sql=sql)])
    assert "SQL: |-\n" in text
    assert yaml.safe_load(text)[0]["SQL"] == "select a\nfrom t\nwhere x = 1"


def test_format_history_failed_turn_shows_error_only():
    history = [_turn(1, sql=None, result_preview=None, error="超时")]
    items = yaml.safe_load(format_history(history))
    assert items == [{"轮次": 1, "问题": "问题1", "结果": "查询失败：超时"}]


def test_format_history_falls_back_to_question():
    items = yaml.safe_load(format_history([_turn(1, standalone="")]))
    assert items[0]["问题"] == "原话1"


def test_parse_rewrite_followup():
    out = {"intent": "followup", "standalone_question": " 2025 年华南的销售额 "}
    assert parse_rewrite(out, "那华南呢") == (INTENT_FOLLOWUP, "2025 年华南的销售额")


def test_parse_rewrite_result_qa():
    out = {"intent": "RESULT_QA", "standalone_question": "销售额最高的大区是哪个"}
    assert parse_rewrite(out, "哪个最高")[0] == INTENT_RESULT_QA


def test_parse_rewrite_new_keeps_user_input():
    # 新问题不允许模型把上文条件带进来
    out = {"intent": "new", "standalone_question": "2025 年铂金会员有多少个"}
    assert parse_rewrite(out, "铂金会员有多少个") == (INTENT_NEW, "铂金会员有多少个")


def test_parse_rewrite_unknown_intent_and_empty_question():
    assert parse_rewrite({"intent": "clarify"}, "看看最近的情况") == (
        INTENT_FOLLOWUP,
        "看看最近的情况",
    )


def test_parse_rewrite_rejects_non_object():
    with pytest.raises(ValueError):
        parse_rewrite(["new"], "q")
