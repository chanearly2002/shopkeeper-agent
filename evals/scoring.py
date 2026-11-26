"""
评测打分与归因

纯函数模块，不依赖数据库和项目配置，方便单元测试：
- attribute_failure：答错时判断第一个出错的环节（参考 Uber QueryGPT 按意图 选表 生成分别统计的做法）
- summarize：按类别 难度 归因环节汇总准确率与成本
- compare_with_baseline：与基线比较，判断是否退化（必过题失败、准确率下降、成本上升）
- summarize_dialogs / render_dialog_markdown：多轮评测的汇总（意图准确率、轮次准确率、整段对话成功率）
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

# 失败环节，按流水线顺序排列；判断时命中第一个即停
STAGE_PASS = "通过"
STAGE_ERROR = "流程异常"
STAGE_INTENT = "意图错误"  # 多轮：本轮意图判断错误（如把追问当成新问题）
STAGE_RECALL = "召回遗漏"
STAGE_FILTER = "过滤误删"
STAGE_INVALID_SQL = "SQL不可执行"
STAGE_WRONG_RESULT = "结果不符"


@dataclass
class CaseResult:
    """一道题（一种问法）的评测结果"""

    case_id: str
    category: str
    difficulty: str
    must_pass: bool
    question: str
    variant: str  # question 或 paraphrase_1 ...
    passed: bool = False
    stage: str = STAGE_WRONG_RESULT
    sql: str | None = None
    error: str | None = None
    # 各环节命中情况，None 表示该答题方式不经过此环节（如直接用标准 SQL 作答）
    recall_missing: list[str] | None = None
    filter_missing: list[str] | None = None
    lint_issues: list[str] = field(default_factory=list)
    correct_attempts: int = 0
    llm_calls: int = 0
    tokens: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def missing_items(expect: dict, available: dict[str, set[str]]) -> list[str]:
    """expect 中的表 / 指标 / 取值有哪些不在 available 里，返回 “类型:名称” 列表"""
    missing = []
    for kind in ("tables", "metrics", "values"):
        for name in expect.get(kind, []) or []:
            if name not in available.get(kind, set()):
                missing.append(f"{kind}:{name}")
    return missing


def attribute_failure(
    *,
    error: str | None,
    sql: str | None,
    recall_missing: list[str] | None,
    filter_missing: list[str] | None,
    sql_executable: bool,
) -> str:
    """答错时按流水线顺序找出第一个出问题的环节"""
    if error and not sql:
        return STAGE_ERROR
    if recall_missing:
        return STAGE_RECALL
    if filter_missing:
        return STAGE_FILTER
    if not sql or not sql_executable:
        return STAGE_INVALID_SQL
    return STAGE_WRONG_RESULT


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def summarize(results: list[CaseResult]) -> dict:
    """汇总整体 分类别 分难度的准确率、失败环节分布和平均成本"""
    total = len(results)
    passed = sum(r.passed for r in results)

    def group(key) -> dict[str, dict]:
        buckets: dict[str, list[CaseResult]] = defaultdict(list)
        for r in results:
            buckets[key(r)].append(r)
        return {
            k: {
                "total": len(v),
                "passed": sum(x.passed for x in v),
                "accuracy": _rate(sum(x.passed for x in v), len(v)),
            }
            for k, v in sorted(buckets.items())
        }

    failures = [r for r in results if not r.passed]
    must_pass = [r for r in results if r.must_pass]
    return {
        "total": total,
        "passed": passed,
        "accuracy": _rate(passed, total),
        "must_pass_failed": sorted({r.case_id for r in must_pass if not r.passed}),
        "by_category": group(lambda r: r.category),
        "by_difficulty": group(lambda r: r.difficulty),
        "failure_stages": dict(Counter(r.stage for r in failures).most_common()),
        "avg_llm_calls": round(sum(r.llm_calls for r in results) / total, 2)
        if total
        else 0,
        "avg_tokens": round(sum(r.tokens for r in results) / total, 1) if total else 0,
        "avg_seconds": round(sum(r.seconds for r in results) / total, 2)
        if total
        else 0,
        "lint_flagged": sum(bool(r.lint_issues) for r in results),
        "per_case": {f"{r.case_id}#{r.variant}": r.passed for r in results},
    }


@dataclass
class RegressionReport:
    """与基线比较的结果"""

    regressed: bool
    reasons: list[str]
    newly_failed: list[str]
    newly_passed: list[str]


def compare_with_baseline(
    current: dict,
    baseline: dict,
    max_accuracy_drop: float = 0.02,
    max_cost_increase: float = 0.3,
) -> RegressionReport:
    """
    判断本次评测相对基线是否退化：
    - 任一必过题失败
    - 整体准确率下降超过 max_accuracy_drop
    - 平均 LLM 调用次数上升超过 max_cost_increase（比例）
    """
    reasons = []
    if current["must_pass_failed"]:
        reasons.append(f"必过题失败：{current['must_pass_failed']}")
    drop = baseline["accuracy"] - current["accuracy"]
    if drop > max_accuracy_drop:
        reasons.append(
            f"准确率下降 {drop:.1%}（{baseline['accuracy']:.1%} → {current['accuracy']:.1%}）"
        )
    base_calls = baseline.get("avg_llm_calls") or 0
    if base_calls and current["avg_llm_calls"] > base_calls * (1 + max_cost_increase):
        reasons.append(
            f"平均 LLM 调用次数上升（{base_calls} → {current['avg_llm_calls']}）"
        )

    base_cases, cur_cases = baseline.get("per_case", {}), current["per_case"]
    common = set(base_cases) & set(cur_cases)
    newly_failed = sorted(k for k in common if base_cases[k] and not cur_cases[k])
    newly_passed = sorted(k for k in common if not base_cases[k] and cur_cases[k])
    return RegressionReport(bool(reasons), reasons, newly_failed, newly_passed)


def render_markdown(
    summary: dict,
    results: list[CaseResult],
    meta: dict,
    regression: RegressionReport | None = None,
) -> str:
    """生成 Markdown 汇总报告"""
    lines = [
        f"# 评测报告 {meta['run_id']}",
        "",
        f"- 答题方式：`{meta['predictor']}`；评测集：`{meta['dataset']}`；题目数：{summary['total']}",
        f"- 配置：few-shot 示例 {'开' if meta.get('examples') else '关'}；SQL 语义检查 {'开' if meta.get('semantic_check') else '关'}",
        f"- **准确率：{summary['accuracy']:.1%}**（{summary['passed']}/{summary['total']}）",
        f"- 必过题失败：{', '.join(summary['must_pass_failed']) or '无'}",
        f"- 平均 LLM 调用 {summary['avg_llm_calls']} 次、{summary['avg_tokens']} tokens、耗时 {summary['avg_seconds']} 秒；"
        f"最终 SQL 仍有语义检查提示的题 {summary['lint_flagged']} 道",
        "",
    ]
    if regression is not None:
        lines += [
            "## 与基线比较",
            "",
            f"- 结论：{'**退化**' if regression.regressed else '未退化'}",
        ]
        lines += [f"- {r}" for r in regression.reasons]
        lines += [
            f"- 由对变错：{', '.join(regression.newly_failed) or '无'}",
            f"- 由错变对：{', '.join(regression.newly_passed) or '无'}",
            "",
        ]
    lines += [
        "## 分类别",
        "",
        "| 类别 | 题数 | 通过 | 准确率 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {k} | {v['total']} | {v['passed']} | {v['accuracy']:.0%} |"
        for k, v in summary["by_category"].items()
    ]
    lines += ["", "## 失败环节分布", "", "| 环节 | 题数 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in summary["failure_stages"].items()] or [
        "| 无 | 0 |"
    ]
    failed = [r for r in results if not r.passed]
    if failed:
        lines += [
            "",
            "## 失败明细",
            "",
            "| 题目 | 问法 | 环节 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for r in failed:
            note = (
                r.error
                or "; ".join((r.recall_missing or []) + (r.filter_missing or []))
                or "; ".join(r.lint_issues)
            )
            note = (note or "").replace("|", "/").replace("\n", " ")[:120]
            lines.append(f"| {r.case_id} | {r.variant} | {r.stage} | {note} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 多轮评测
# ---------------------------------------------------------------------------

# 多轮评测的两种模式
MODE_TEACHER = "teacher"  # 历史用标准答案填充，测单轮追问能力，前一轮答错不连累后面
MODE_FREE = "free"  # 用模型自己的历史，测整段对话的真实体验


@dataclass
class TurnResult(CaseResult):
    """多轮对话中一轮的评测结果；variant 固定为 turn_<轮次>"""

    turn: int = 1
    gold_intent: str = "new"
    intent: str | None = None  # 模型判断的意图；不经过改写环节时为 None
    gold_standalone: str | None = None
    standalone: str | None = None  # 模型改写后的问题
    answer: str | None = None  # 直接基于上一轮结果回答的文本（没有 SQL）
    clarify_question: str | None = None  # 模型反问的问题；没有反问时为 None

    @property
    def intent_correct(self) -> bool | None:
        return None if self.intent is None else self.intent == self.gold_intent

    def to_dict(self) -> dict:
        return {**asdict(self), "intent_correct": self.intent_correct}


def attribute_turn_failure(
    *,
    intent_correct: bool | None,
    error: str | None,
    sql: str | None,
    recall_missing: list[str] | None,
    filter_missing: list[str] | None,
    sql_executable: bool,
) -> str:
    """多轮的一轮答错时，先看意图是否判断错，再按单轮流水线顺序归因"""
    if error and not sql:
        return STAGE_ERROR
    if intent_correct is False:
        return STAGE_INTENT
    return attribute_failure(
        error=error,
        sql=sql,
        recall_missing=recall_missing,
        filter_missing=filter_missing,
        sql_executable=sql_executable,
    )


def summarize_dialogs(results: list[TurnResult]) -> dict:
    """
    汇总多轮评测

    - accuracy：全部轮次的执行准确率（与单轮汇总同名，便于复用基线比较）
    - followup_accuracy：只看第 2 轮及以后，衡量追问能力
    - intent_accuracy：第 2 轮及以后意图判断正确的比例（首轮意图固定为 new，不计入）
    - dialog_success_rate：整段对话每一轮都答对的比例
    """
    total = len(results)
    passed = sum(r.passed for r in results)
    followups = [r for r in results if r.turn > 1]
    judged = [r for r in followups if r.intent is not None]

    dialogs: dict[str, list[TurnResult]] = defaultdict(list)
    for r in results:
        dialogs[r.case_id].append(r)
    dialog_ok = {cid: all(t.passed for t in turns) for cid, turns in dialogs.items()}

    by_category: dict[str, dict] = {}
    for cid, turns in sorted(dialogs.items()):
        bucket = by_category.setdefault(
            turns[0].category,
            {"dialogs": 0, "success": 0, "followups": 0, "followups_passed": 0},
        )
        bucket["dialogs"] += 1
        bucket["success"] += dialog_ok[cid]
        bucket["followups"] += sum(t.turn > 1 for t in turns)
        bucket["followups_passed"] += sum(t.turn > 1 and t.passed for t in turns)
    for bucket in by_category.values():
        bucket["dialog_success_rate"] = _rate(bucket["success"], bucket["dialogs"])
        bucket["followup_accuracy"] = _rate(
            bucket["followups_passed"], bucket["followups"]
        )

    confusion = Counter(
        f"{r.gold_intent}→{r.intent}" for r in judged if r.intent != r.gold_intent
    )
    failures = [r for r in results if not r.passed]
    return {
        "total": total,
        "passed": passed,
        "accuracy": _rate(passed, total),
        # 直接基于上一轮结果回答的轮数、发起反问的轮数（含不该问却问了的）
        "answered_turns": sum(bool(r.answer and not r.sql) for r in results),
        "clarify_turns": sum(bool(r.clarify_question) for r in results),
        "followup_total": len(followups),
        "followup_passed": sum(r.passed for r in followups),
        "followup_accuracy": _rate(sum(r.passed for r in followups), len(followups)),
        "intent_total": len(judged),
        "intent_accuracy": _rate(
            sum(bool(r.intent_correct) for r in judged), len(judged)
        )
        if judged
        else None,
        "intent_confusion": dict(confusion.most_common()),
        "dialogs": len(dialogs),
        "dialog_success": sum(dialog_ok.values()),
        "dialog_success_rate": _rate(sum(dialog_ok.values()), len(dialogs)),
        "must_pass_failed": sorted(
            {r.case_id for r in results if r.must_pass and not dialog_ok[r.case_id]}
        ),
        "by_category": by_category,
        "failure_stages": dict(Counter(r.stage for r in failures).most_common()),
        "avg_llm_calls": round(sum(r.llm_calls for r in results) / total, 2)
        if total
        else 0,
        "avg_tokens": round(sum(r.tokens for r in results) / total, 1) if total else 0,
        "avg_seconds": round(sum(r.seconds for r in results) / total, 2)
        if total
        else 0,
        "lint_flagged": sum(bool(r.lint_issues) for r in results),
        "per_case": {f"{r.case_id}#{r.turn}": r.passed for r in results},
    }


def render_dialog_markdown(
    summary: dict,
    results: list[TurnResult],
    meta: dict,
    regression: RegressionReport | None = None,
) -> str:
    """生成多轮评测的 Markdown 报告"""
    mode = meta.get("mode")
    mode_text = {
        MODE_TEACHER: "teacher forcing（历史用标准答案填充）",
        MODE_FREE: "自由对话（使用模型自己的历史）",
    }.get(mode, str(mode))
    intent = summary["intent_accuracy"]
    lines = [
        f"# 多轮评测报告 {meta['run_id']}",
        "",
        f"- 答题方式：`{meta['predictor']}`；模式：{mode_text}；评测集：`{meta['dataset']}`",
        f"- **整段对话成功率：{summary['dialog_success_rate']:.1%}**（{summary['dialog_success']}/{summary['dialogs']}）",
        f"- **追问轮准确率：{summary['followup_accuracy']:.1%}**（{summary['followup_passed']}/{summary['followup_total']}）；"
        f"全部轮次准确率 {summary['accuracy']:.1%}（{summary['passed']}/{summary['total']}）",
        "- 意图准确率："
        + (
            f"{intent:.1%}（{summary['intent_total']} 个追问轮）"
            if intent is not None
            else "未评估（答题方式不经过改写环节）"
        ),
        f"- 必过对话失败：{', '.join(summary['must_pass_failed']) or '无'}",
        f"- 每轮平均 LLM 调用 {summary['avg_llm_calls']} 次、{summary['avg_tokens']} tokens、耗时 {summary['avg_seconds']} 秒",
        f"- 直接基于上一轮结果回答 {summary.get('answered_turns', 0)} 轮，发起反问 {summary.get('clarify_turns', 0)} 轮",
        "",
    ]
    if mode == MODE_TEACHER:
        lines += [
            "> teacher forcing 下每一轮都基于标准历史作答，整段对话成功率只说明“每一轮单独都答对”，"
            "真实体验以自由对话模式为准",
            "",
        ]
    if summary["intent_confusion"]:
        lines += ["## 意图混淆", "", "| 标准 → 预测 | 次数 |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in summary["intent_confusion"].items()]
        lines.append("")
    if regression is not None:
        lines += [
            "## 与基线比较",
            "",
            f"- 结论：{'**退化**' if regression.regressed else '未退化'}",
        ]
        lines += [f"- {r}" for r in regression.reasons]
        lines += [
            f"- 由对变错：{', '.join(regression.newly_failed) or '无'}",
            f"- 由错变对：{', '.join(regression.newly_passed) or '无'}",
            "",
        ]
    lines += [
        "## 分类别",
        "",
        "| 类别 | 对话数 | 整段成功 | 追问轮准确率 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {k} | {v['dialogs']} | {v['success']}（{v['dialog_success_rate']:.0%}） | "
        f"{v['followups_passed']}/{v['followups']}（{v['followup_accuracy']:.0%}） |"
        for k, v in summary["by_category"].items()
    ]
    lines += ["", "## 失败环节分布", "", "| 环节 | 轮数 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in summary["failure_stages"].items()] or [
        "| 无 | 0 |"
    ]
    failed = [r for r in results if not r.passed]
    if failed:
        lines += [
            "",
            "## 失败明细",
            "",
            "| 对话 | 轮次 | 环节 | 意图（标准/预测） | 改写（预测 / 标准） | 说明 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in failed:
            note = (
                r.error
                or (f"回答：{r.answer}" if r.answer else "")
                or "; ".join((r.recall_missing or []) + (r.filter_missing or []))
                or "; ".join(r.lint_issues)
            )
            if r.clarify_question:
                note = f"反问：{r.clarify_question}；{note}"
            rewrite = (
                f"{r.standalone} / {r.gold_standalone}"
                if r.standalone is not None and r.turn > 1
                else ""
            )
            cells = [
                r.case_id,
                str(r.turn),
                r.stage,
                f"{r.gold_intent}/{r.intent or '-'}",
                rewrite,
                note or "",
            ]
            cells = [c.replace("|", "/").replace("\n", " ")[:120] for c in cells]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
