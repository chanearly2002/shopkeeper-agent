"""评测打分 归因 基线比较 与 LLM 缓存的测试"""

from langchain_core.outputs import Generation

from evals.llm_cache import SQLiteLLMCache
from evals.scoring import (
    MODE_TEACHER,
    STAGE_ERROR,
    STAGE_FILTER,
    STAGE_INTENT,
    STAGE_INVALID_SQL,
    STAGE_PASS,
    STAGE_RECALL,
    STAGE_WRONG_RESULT,
    CaseResult,
    TurnResult,
    attribute_failure,
    attribute_turn_failure,
    compare_with_baseline,
    missing_items,
    render_dialog_markdown,
    render_markdown,
    summarize,
    summarize_dialogs,
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


# ---------------------------------------------------------------------------
# 多轮评测
# ---------------------------------------------------------------------------


def _turn_result(case_id, turn, passed, gold_intent="followup", intent=None, **kw):
    return TurnResult(
        case_id=case_id,
        category=kw.pop("category", "ellipsis"),
        difficulty="easy",
        must_pass=kw.pop("must_pass", False),
        question=f"q{turn}",
        variant=f"turn_{turn}",
        passed=passed,
        stage=STAGE_PASS if passed else kw.pop("stage", STAGE_WRONG_RESULT),
        turn=turn,
        gold_intent=gold_intent if turn > 1 else "new",
        intent=intent,
        llm_calls=7 if turn > 1 else 6,
        **kw,
    )


def test_turn_attribution_checks_intent_first():
    kwargs = dict(
        error=None,
        sql="select 1",
        recall_missing=["tables:dim_user"],
        filter_missing=[],
        sql_executable=True,
    )
    assert attribute_turn_failure(intent_correct=False, **kwargs) == STAGE_INTENT
    # 意图对了再按单轮流水线顺序归因；不经过改写环节（None）也一样
    assert attribute_turn_failure(intent_correct=True, **kwargs) == STAGE_RECALL
    assert attribute_turn_failure(intent_correct=None, **kwargs) == STAGE_RECALL
    # 流程异常优先于意图错误
    kwargs.update(error="boom", sql=None)
    assert attribute_turn_failure(intent_correct=False, **kwargs) == STAGE_ERROR


def test_summarize_dialogs():
    results = [
        # 对话 a：两轮全对，意图正确
        _turn_result("a", 1, True, intent="new", must_pass=True),
        _turn_result("a", 2, True, intent="followup", must_pass=True),
        # 对话 b：追问轮把 result_qa 判成了 new 并答错
        _turn_result("b", 1, True, intent="new", category="result_ref"),
        _turn_result(
            "b",
            2,
            False,
            gold_intent="result_qa",
            intent="new",
            stage=STAGE_INTENT,
            category="result_ref",
        ),
        # 对话 c：第 3 轮意图对但结果错
        _turn_result("c", 1, True, intent="new", must_pass=True),
        _turn_result("c", 2, True, intent="followup", must_pass=True),
        _turn_result("c", 3, False, intent="followup", must_pass=True),
    ]
    s = summarize_dialogs(results)
    assert (s["total"], s["passed"]) == (7, 5)
    assert (s["followup_total"], s["followup_passed"]) == (4, 2)
    assert s["followup_accuracy"] == 0.5
    # 首轮不计入意图准确率
    assert (s["intent_total"], s["intent_accuracy"]) == (4, 0.75)
    assert s["intent_confusion"] == {"result_qa→new": 1}
    assert (s["dialogs"], s["dialog_success"]) == (3, 1)
    assert s["must_pass_failed"] == ["c"]
    assert s["by_category"]["result_ref"]["followup_accuracy"] == 0.0
    assert s["by_category"]["ellipsis"]["dialog_success_rate"] == 0.5
    assert s["failure_stages"] == {STAGE_INTENT: 1, STAGE_WRONG_RESULT: 1}
    assert s["per_case"]["c#3"] is False
    assert s["avg_llm_calls"] == round((6 * 3 + 7 * 4) / 7, 2)


def test_summarize_dialogs_without_intent():
    # gold / trap 方式不经过改写环节，意图准确率记为未评估
    s = summarize_dialogs([_turn_result("a", 1, True), _turn_result("a", 2, False)])
    assert s["intent_accuracy"] is None
    assert s["intent_total"] == 0


def test_dialog_summary_works_with_baseline_and_report():
    good = summarize_dialogs(
        [_turn_result("a", 1, True, must_pass=True), _turn_result("a", 2, True)]
    )
    bad = summarize_dialogs(
        [
            _turn_result("a", 1, True, must_pass=True),
            _turn_result("a", 2, False, must_pass=True, intent="new"),
        ]
    )
    report = compare_with_baseline(bad, good)
    assert report.regressed
    assert report.newly_failed == ["a#2"]

    results = [
        _turn_result("a", 1, True, intent="new"),
        _turn_result(
            "a", 2, False, intent="new", stage=STAGE_INTENT, standalone="那华南呢"
        ),
    ]
    md = render_dialog_markdown(
        summarize_dialogs(results),
        results,
        {"run_id": "x", "predictor": "agent", "dataset": "d", "mode": MODE_TEACHER},
        report,
    )
    assert "整段对话成功率" in md and "意图混淆" in md and "teacher forcing" in md
    assert "那华南呢" in md
    assert results[1].to_dict()["intent_correct"] is False
