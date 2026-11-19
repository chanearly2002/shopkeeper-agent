"""评测打分 归因 基线比较 与 LLM 缓存的测试"""

from langchain_core.outputs import Generation

from evals.llm_cache import SQLiteLLMCache
from evals.scoring import (
    STAGE_ERROR,
    STAGE_FILTER,
    STAGE_INVALID_SQL,
    STAGE_PASS,
    STAGE_RECALL,
    STAGE_WRONG_RESULT,
    CaseResult,
    attribute_failure,
    compare_with_baseline,
    missing_items,
    render_markdown,
    summarize,
)


def test_missing_items():
    expect = {
        "tables": ["fact_order", "dim_region"],
        "metrics": ["支付金额"],
        "values": ["上海市"],
    }
    available = {"tables": {"fact_order"}, "metrics": {"支付金额"}, "values": set()}
    assert missing_items(expect, available) == ["tables:dim_region", "values:上海市"]


def test_attribution_order():
    kwargs = dict(
        error=None,
        sql="select 1",
        recall_missing=[],
        filter_missing=[],
        sql_executable=True,
    )
    assert attribute_failure(**kwargs) == STAGE_WRONG_RESULT
    assert attribute_failure(**{**kwargs, "sql_executable": False}) == STAGE_INVALID_SQL
    assert (
        attribute_failure(**{**kwargs, "filter_missing": ["tables:x"]}) == STAGE_FILTER
    )
    assert (
        attribute_failure(
            **{**kwargs, "recall_missing": ["tables:x"], "filter_missing": ["tables:x"]}
        )
        == STAGE_RECALL
    )
    assert attribute_failure(**{**kwargs, "error": "boom", "sql": None}) == STAGE_ERROR


def _result(case_id, passed, category="c", must_pass=False, stage=None, calls=6):
    return CaseResult(
        case_id=case_id,
        category=category,
        difficulty="easy",
        must_pass=must_pass,
        question="q",
        variant="question",
        passed=passed,
        stage=stage or (STAGE_PASS if passed else STAGE_WRONG_RESULT),
        llm_calls=calls,
    )


def test_summarize():
    results = [
        _result("a", True),
        _result("b", False, must_pass=True, stage=STAGE_RECALL),
        _result("c", True, category="d"),
    ]
    s = summarize(list(results))
    assert s["accuracy"] == round(2 / 3, 4)
    assert s["must_pass_failed"] == ["b"]
    assert s["by_category"]["c"] == {"total": 2, "passed": 1, "accuracy": 0.5}
    assert s["failure_stages"] == {STAGE_RECALL: 1}
    assert "## 失败明细" in render_markdown(
        s, list(results), {"run_id": "x", "predictor": "agent", "dataset": "d"}
    )


def test_compare_with_baseline():
    base = summarize([_result("a", True), _result("b", True), _result("c", False)])
    same = compare_with_baseline(base, base)
    assert not same.regressed
    worse = summarize([_result("a", True), _result("b", False), _result("c", True)])
    report = compare_with_baseline(worse, base, max_accuracy_drop=0.5)
    assert not report.regressed  # 准确率持平
    assert report.newly_failed == ["b#question"] and report.newly_passed == [
        "c#question"
    ]
    dropped = summarize([_result("a", False), _result("b", False), _result("c", False)])
    assert compare_with_baseline(dropped, base).regressed
    costly = summarize(
        [
            _result("a", True, calls=12),
            _result("b", True, calls=12),
            _result("c", False, calls=12),
        ]
    )
    assert compare_with_baseline(costly, base).regressed
    must = summarize(
        [_result("a", False, must_pass=True), _result("b", True), _result("c", True)]
    )
    assert compare_with_baseline(must, base).regressed


def test_llm_cache_roundtrip(tmp_path):
    cache = SQLiteLLMCache(tmp_path / "cache.sqlite")
    assert cache.lookup("prompt", "llm") is None
    cache.update("prompt", "llm", [Generation(text="select 1")])
    again = SQLiteLLMCache(tmp_path / "cache.sqlite")
    assert again.lookup("prompt", "llm")[0].text == "select 1"
    assert again.lookup("prompt", "other-llm") is None
    assert (cache.misses, again.hits) == (1, 1)
