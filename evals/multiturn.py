"""
多轮评测：用会话图逐轮作答、打分、归因

由 evals/runner.py 的 --multiturn 调用，两种模式（借鉴 BIRD-Interact、CoSQL）：
- teacher：每一轮都在一个新会话里作答，历史用前几轮的标准答案（标准改写、标准意图、标准 SQL
  及其结果预览）填充，前一轮答错不会连累后面，测的是“单轮追问能力”
- free：整段对话用同一个 thread_id 依次作答，历史是模型自己的，测的是真实体验

每一轮记录意图、改写后的问题、最终 SQL，并用 evals/compare.py 与标准 SQL 比较结果集；
单轮流水线各节点的输出通过 subgraphs=True 的流式更新捕获，失败时与单轮评测一样归因到具体环节
"""

import time
import uuid

from langgraph.types import Command

from app.agent.session_graph import session_graph
from app.agent.state import Turn
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.conf.app_config import app_config
from app.core.conversation import INTENT_NEW, rows_for_answer, summarize_result
from app.services.query_service import QueryService
from evals.compare import results_equal
from evals.runner import UsageCallback, available_items, execute
from evals.scoring import (
    MODE_TEACHER,
    STAGE_INTENT,
    STAGE_PASS,
    STAGE_WRONG_RESULT,
    TurnResult,
    attribute_turn_failure,
    missing_items,
)

# 模型在不该反问的轮次反问时，评测扮演的用户统一这样回答，让对话能继续下去
DEFAULT_CLARIFY_REPLY = "按你的理解直接查询"


async def run_session_turn(
    thread_id: str,
    question: str,
    today: str,
    seed_history: list[Turn] | None = None,
    seed_last_result: list[dict] | None = None,
    clarify_reply: str | None = None,
) -> dict:
    """
    在会话里回答一轮，返回意图、改写、最终 SQL 或直接回答，以及单轮流水线各节点的输出

    - seed_history / seed_last_result 只在 teacher 模式下使用：作为新会话的初始历史和上一轮结果
    - 会话图停在反问处时，扮演用户用 clarify_reply 回答（没有标注时用默认回答）并恢复执行，
      每轮最多恢复一次；意图按第一次判断计（是否该反问），改写结果按最后一次计
    """
    usage = UsageCallback()
    pipeline_outputs: dict[str, dict] = {}
    session_outputs: dict[str, dict] = {}
    intents: list[str] = []
    clarify_question = None
    error = None
    started = time.perf_counter()
    graph_input = {
        "user_input": question,
        "today": today,
        "clarified": False,
        "clarification": None,
    }
    if seed_history:
        graph_input["history"] = seed_history
    if seed_last_result is not None:
        graph_input["last_result"] = seed_last_result
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [usage]}
    async with (
        meta_mysql_client_manager.session_factory() as meta_session,
        dw_mysql_client_manager.session_factory() as dw_session,
    ):
        context = QueryService.from_sessions(meta_session, dw_session).build_context()
        try:
            for _ in range(2):
                interrupted = None
                async for namespace, mode, chunk in session_graph.astream(
                    input=graph_input,
                    config=config,
                    context=context,
                    stream_mode=["updates", "custom"],
                    subgraphs=True,
                ):
                    if mode != "updates":
                        continue
                    if not namespace and "__interrupt__" in chunk:
                        interrupted = chunk["__interrupt__"][0].value
                        continue
                    # namespace 为空是会话图自己的节点，否则是 run_pipeline 里单轮图的节点
                    target = pipeline_outputs if namespace else session_outputs
                    for node, update in chunk.items():
                        if isinstance(update, dict):
                            target[node] = update
                            if node == "contextualize" and not namespace:
                                intents.append(update.get("intent"))
                if interrupted is None:
                    break
                clarify_question = interrupted.get("question")
                graph_input = Command(resume=clarify_reply or DEFAULT_CLARIFY_REPLY)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

    rewrite = session_outputs.get("contextualize") or {}
    pipeline = session_outputs.get("run_pipeline") or {}
    answer = (session_outputs.get("answer_from_result") or {}).get("answer")
    # 修正次数用完时 run_pipeline 不返回 SQL，此时用最后一版候选 SQL 判断是否可执行
    sql = pipeline.get("sql") or (
        pipeline_outputs.get("correct_sql")
        or pipeline_outputs.get("generate_sql")
        or {}
    ).get("sql")
    return {
        "intent": intents[0] if intents else None,
        "standalone": rewrite.get("standalone"),
        "clarify_question": clarify_question,
        "answer": answer if not sql else None,
        "sql": sql,
        "error": error or pipeline.get("error"),
        "outputs": pipeline_outputs,
        "llm_calls": usage.calls,
        "tokens": usage.tokens,
        "seconds": time.perf_counter() - started,
    }


def gold_turn(turn: dict, index: int, rows: list[dict], today: str) -> Turn:
    """把评测集中的一轮标准答案转成会话历史，供 teacher 模式填充"""
    return Turn(
        question=turn["question"],
        standalone=turn.get("gold_standalone") or turn["question"],
        intent=turn.get("gold_intent", INTENT_NEW) if index > 0 else INTENT_NEW,
        sql=turn["gold_sql"].strip(),
        result_preview=summarize_result(
            rows, False, app_config.conversation.result_preview_rows
        ),
        error=None,
        answer=None,
        clarification=turn.get("clarify_reply"),
        today=today,
    )


async def evaluate_dialog(
    item: dict, predictor: str, mode: str, gold_cache: dict
) -> list[TurnResult]:
    """按顺序回答一段对话的每一轮并打分"""
    today = item.get("today") or app_config.agent.today
    thread_id = uuid.uuid4().hex
    results: list[TurnResult] = []
    for t, turn in enumerate(item["turns"]):
        gold_rows = gold_cache[(item["id"], t)]
        result = TurnResult(
            case_id=item["id"],
            category=item["category"],
            difficulty=item["difficulty"],
            must_pass=bool(item.get("must_pass")),
            question=turn["question"],
            variant=f"turn_{t + 1}",
            turn=t + 1,
            gold_intent=turn.get("gold_intent", INTENT_NEW) if t > 0 else INTENT_NEW,
            gold_standalone=turn.get("gold_standalone") or turn["question"],
        )

        if predictor == "agent":
            seed, seed_last = None, None
            if mode == MODE_TEACHER:
                # 每轮一个新会话，历史是前几轮的标准答案，上一轮结果是标准 SQL 的结果
                thread_id = uuid.uuid4().hex
                seed = [
                    gold_turn(prev, i, gold_cache[(item["id"], i)], today)
                    for i, prev in enumerate(item["turns"][:t])
                ]
                if t > 0:
                    seed_last = rows_for_answer(
                        gold_cache[(item["id"], t - 1)],
                        False,
                        app_config.conversation.answer_max_rows,
                    )
            answer = await run_session_turn(
                thread_id,
                turn["question"],
                today,
                seed,
                seed_last,
                turn.get("clarify_reply"),
            )
            result.intent, result.standalone = answer["intent"], answer["standalone"]
            result.sql, result.error = answer["sql"], answer["error"]
            result.answer = answer["answer"]
            result.clarify_question = answer["clarify_question"]
            result.llm_calls, result.tokens = answer["llm_calls"], answer["tokens"]
            result.seconds = round(answer["seconds"], 2)
            outputs = answer["outputs"]
            result.lint_issues = (outputs.get("validate_sql") or {}).get(
                "lint_issues"
            ) or []
            result.correct_attempts = (outputs.get("correct_sql") or {}).get(
                "correct_attempts", 0
            )
            recalled, filtered = available_items(outputs)
            expect = turn.get("expect") or {}
            result.recall_missing = missing_items(expect, recalled)
            result.filter_missing = missing_items(
                {k: v for k, v in expect.items() if k != "values"}, filtered
            )
        elif predictor == "gold":
            # 自检：标准答案作答，意图和改写也按标准答案计
            result.sql = turn["gold_sql"]
            result.intent, result.standalone = (
                result.gold_intent,
                result.gold_standalone,
            )
        else:
            result.sql = turn["trap_sql"]

        executable = False
        if result.answer and not result.sql:
            # 直接基于上一轮结果用文字回答：回答里要出现标注的关键内容
            contains = turn.get("gold_answer_contains") or []
            result.passed = bool(contains) and all(c in result.answer for c in contains)
        elif result.sql:
            try:
                pred_rows = await execute(result.sql)
                executable = True
                result.passed = results_equal(
                    pred_rows, gold_rows, bool(turn.get("order_matters"))
                )
            except Exception as e:
                result.error = (
                    result.error or f"执行失败：{getattr(e, 'orig', None) or e}"
                )
        result.stage = (
            STAGE_PASS
            if result.passed
            else answer_failure_stage(result)
            if result.answer and not result.sql
            else attribute_turn_failure(
                intent_correct=result.intent_correct,
                error=result.error,
                sql=result.sql,
                recall_missing=result.recall_missing,
                filter_missing=result.filter_missing,
                sql_executable=executable,
            )
        )
        results.append(result)
    return results


def answer_failure_stage(result: TurnResult) -> str:
    """直接回答答错时的归因：意图判错优先，否则就是回答的内容不对"""
    return STAGE_INTENT if result.intent_correct is False else STAGE_WRONG_RESULT
