"""
评测 runner：答题、打分、归因、与基线比较

用法：
    uv run python -m evals.runner                          # 问数 Agent 答全部题目
    uv run python -m evals.runner --paraphrases            # 同时用每题的其他问法再答一遍
    uv run python -m evals.runner --category fanout --ids fanout_01
    uv run python -m evals.runner --no-examples            # 关掉 few-shot 示例做消融对比
    uv run python -m evals.runner --llm-cache evals/.cache/llm.sqlite   # 录制 / 回放 LLM 调用
    uv run python -m evals.runner --baseline evals/baseline.json        # 与基线比较，退化时退出码为 1
    uv run python -m evals.runner --baseline evals/baseline.json --save-baseline
    uv run python -m evals.runner --predictor gold         # 直接用标准 SQL 作答，应为 100%
    uv run python -m evals.runner --predictor trap         # 直接用错误写法作答，应为 0%

打分方式参照 Databricks Genie 的 benchmark：执行模型 SQL 与标准 SQL，用 evals/compare.py 比较结果集
归因参照 Uber QueryGPT：捕获问数图每个节点的输出，判断答错时是召回漏了、过滤删了，还是 SQL 写错了
报告写到 evals/reports/<时间戳>/：results.jsonl 明细、summary.json 汇总、summary.md 可读报告
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.globals import set_llm_cache
from sqlalchemy import text

from app.agent.graph import graph
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.conf.app_config import app_config
from app.core.sql_safety import ensure_readonly
from app.services.query_service import QueryService
from evals.compare import results_equal
from evals.dataset import DEFAULT_DATASET, ROOT, load_dataset
from evals.llm_cache import SQLiteLLMCache
from evals.scoring import (
    STAGE_PASS,
    CaseResult,
    attribute_failure,
    compare_with_baseline,
    missing_items,
    render_markdown,
    summarize,
)

REPORT_DIR = ROOT / "evals" / "reports"


class UsageCallback(AsyncCallbackHandler):
    """统计一次问数中的 LLM 调用次数和 token 数"""

    def __init__(self):
        self.calls = 0
        self.tokens = 0

    async def on_chat_model_start(self, *args, **kwargs):
        self.calls += 1

    async def on_llm_start(self, *args, **kwargs):
        self.calls += 1

    async def on_llm_end(self, response, **kwargs):
        usage = (response.llm_output or {}).get("token_usage") or {}
        total = usage.get("total_tokens")
        if total is None:
            for generations in response.generations:
                for g in generations:
                    meta = (
                        getattr(getattr(g, "message", None), "usage_metadata", None)
                        or {}
                    )
                    total = (total or 0) + meta.get("total_tokens", 0)
        self.tokens += total or 0


async def run_agent(question: str, today: str) -> dict:
    """用问数图回答一个问题，返回最终 SQL 以及各节点的输出，供打分和归因使用"""
    outputs: dict[str, dict] = {}
    usage = UsageCallback()
    error = None
    started = time.perf_counter()
    async with (
        meta_mysql_client_manager.session_factory() as meta_session,
        dw_mysql_client_manager.session_factory() as dw_session,
    ):
        context = QueryService.from_sessions(meta_session, dw_session).build_context()
        try:
            async for mode, chunk in graph.astream(
                input={"query": question, "today": today},
                context=context,
                stream_mode=["updates", "custom"],
                config={"callbacks": [usage]},
            ):
                if mode == "updates":
                    for node, update in chunk.items():
                        if isinstance(update, dict):
                            outputs[node] = update
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
    # 校正后的 SQL 是最后一版；校正节点没有执行过时，用生成节点的 SQL
    sql = (outputs.get("correct_sql") or outputs.get("generate_sql") or {}).get("sql")
    return {
        "sql": sql,
        "error": error,
        "outputs": outputs,
        "llm_calls": usage.calls,
        "tokens": usage.tokens,
        "seconds": time.perf_counter() - started,
    }


def available_items(outputs: dict[str, dict]) -> tuple[dict, dict]:
    """从节点输出中整理召回阶段和过滤阶段拿到的表 / 指标 / 取值"""
    merge = outputs.get("merge_retrieved_info") or {}
    recalled = {
        "tables": {t["name"] for t in merge.get("table_infos", [])},
        "metrics": {m["name"] for m in merge.get("metric_infos", [])},
        "values": {
            str(v.value)
            for v in (outputs.get("recall_value") or {}).get(
                "retrieved_value_infos", []
            )
        },
    }
    filtered = {
        "tables": {
            t["name"]
            for t in (outputs.get("filter_table") or {}).get("table_infos", [])
        },
        "metrics": {
            m["name"]
            for m in (outputs.get("filter_metric") or {}).get("metric_infos", [])
        },
    }
    return recalled, filtered


async def execute(sql: str) -> list[dict]:
    """在数仓上执行一条只读 SQL"""
    ensure_readonly(sql)
    async with dw_mysql_client_manager.session_factory() as session:
        result = await session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]


async def evaluate_case(
    item: dict, variant: str, question: str, predictor: str, gold_cache: dict
) -> CaseResult:
    """答一道题并打分"""
    turn = item["turns"][0]
    result = CaseResult(
        case_id=item["id"],
        category=item["category"],
        difficulty=item["difficulty"],
        must_pass=bool(item.get("must_pass")),
        question=question,
        variant=variant,
    )
    if item["id"] not in gold_cache:
        gold_cache[item["id"]] = await execute(turn["gold_sql"])
    gold_rows = gold_cache[item["id"]]

    if predictor == "agent":
        answer = await run_agent(question, item.get("today") or app_config.agent.today)
        result.sql, result.error = answer["sql"], answer["error"]
        result.llm_calls, result.tokens = answer["llm_calls"], answer["tokens"]
        result.seconds = round(answer["seconds"], 2)
        validate = answer["outputs"].get("validate_sql") or {}
        result.lint_issues = validate.get("lint_issues") or []
        result.correct_attempts = (answer["outputs"].get("correct_sql") or {}).get(
            "correct_attempts", 0
        )
        recalled, filtered = available_items(answer["outputs"])
        expect = turn.get("expect") or {}
        result.recall_missing = missing_items(expect, recalled)
        result.filter_missing = missing_items(
            {k: v for k, v in expect.items() if k != "values"}, filtered
        )
    else:
        result.sql = turn["gold_sql"] if predictor == "gold" else turn["trap_sql"]

    executable = False
    if result.sql:
        try:
            pred_rows = await execute(result.sql)
            executable = True
            result.passed = results_equal(
                pred_rows, gold_rows, bool(turn.get("order_matters"))
            )
        except Exception as e:
            result.error = result.error or f"执行失败：{getattr(e, 'orig', None) or e}"

    result.stage = (
        STAGE_PASS
        if result.passed
        else attribute_failure(
            error=result.error,
            sql=result.sql,
            recall_missing=result.recall_missing,
            filter_missing=result.filter_missing,
            sql_executable=executable,
        )
    )
    return result


def select_items(
    items: list[dict], ids: set[str] | None, category: str | None
) -> list[dict]:
    """按 id 或类别筛选题目；多轮题暂不支持，跳过"""
    selected = []
    for item in items:
        if ids and item["id"] not in ids:
            continue
        if category and item["category"] != category:
            continue
        if len(item["turns"]) > 1:
            print(f"跳过多轮题 {item['id']}：runner 暂只支持单轮")
            continue
        selected.append(item)
    return selected


async def run(args) -> int:
    items = select_items(
        load_dataset(args.dataset),
        set(args.ids.split(",")) if args.ids else None,
        args.category,
    )
    tasks = []
    for item in items:
        turn = item["turns"][0]
        tasks.append((item, "question", turn["question"]))
        if args.paraphrases:
            for i, p in enumerate(turn.get("paraphrases", []), start=1):
                tasks.append((item, f"paraphrase_{i}", p))

    # 消融开关直接改运行时配置，节点每次执行都会读取
    app_config.knowledge.enable_examples = not args.no_examples
    app_config.agent.semantic_check = not args.no_semantic_check
    cache = None
    if args.llm_cache:
        cache = SQLiteLLMCache(Path(args.llm_cache))
        set_llm_cache(cache)

    dw_mysql_client_manager.init()
    if args.predictor == "agent":
        meta_mysql_client_manager.init()
        qdrant_client_manager.init()
        es_client_manager.init()
        embedding_client_manager.init()

    gold_cache: dict[str, list[dict]] = {}
    semaphore = asyncio.Semaphore(args.concurrency)
    done = 0

    async def worker(item, variant, question):
        nonlocal done
        async with semaphore:
            r = await evaluate_case(item, variant, question, args.predictor, gold_cache)
            done += 1
            print(
                f"[{done}/{len(tasks)}] {r.case_id}#{r.variant} {'✓' if r.passed else '✗ ' + r.stage}"
            )
            return r

    try:
        # 先串行算出所有标准答案，避免并发时重复执行
        for item in items:
            gold_cache[item["id"]] = await execute(item["turns"][0]["gold_sql"])
        results = await asyncio.gather(*(worker(*t) for t in tasks))
    finally:
        await dw_mysql_client_manager.close()
        if args.predictor == "agent":
            await meta_mysql_client_manager.close()
            await qdrant_client_manager.close()
            await es_client_manager.close()

    summary = summarize(results)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {
        "run_id": run_id,
        "predictor": args.predictor,
        "dataset": str(args.dataset.resolve().relative_to(ROOT)),
        "examples": app_config.knowledge.enable_examples,
        "semantic_check": app_config.agent.semantic_check,
        "llm_model": app_config.llm.model_name,
    }
    if cache:
        meta["llm_cache"] = {"hits": cache.hits, "misses": cache.misses}

    regression = None
    baseline_path = Path(args.baseline) if args.baseline else None
    if baseline_path and baseline_path.exists() and not args.save_baseline:
        regression = compare_with_baseline(
            summary,
            json.loads(baseline_path.read_text(encoding="utf-8")),
            args.max_accuracy_drop,
        )

    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False, default=str) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, **summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = render_markdown(summary, results, meta, regression)
    (out_dir / "summary.md").write_text(report, encoding="utf-8")
    if args.save_baseline and baseline_path:
        baseline_path.write_text(
            json.dumps({"meta": meta, **summary}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已保存基线：{baseline_path}")

    print("\n" + report)
    print(f"报告目录：{out_dir}")
    return 1 if regression and regression.regressed else 0


def main():
    parser = argparse.ArgumentParser(description="问数 Agent 评测 runner")
    parser.add_argument("-d", "--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--predictor", choices=["agent", "gold", "trap"], default="agent"
    )
    parser.add_argument("--ids", help="只评测这些题，逗号分隔")
    parser.add_argument("--category", help="只评测某个类别")
    parser.add_argument(
        "--paraphrases", action="store_true", help="同时用每题的其他问法作答"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--no-examples", action="store_true", help="关闭 few-shot 示例召回"
    )
    parser.add_argument(
        "--no-semantic-check", action="store_true", help="关闭 SQL 语义检查"
    )
    parser.add_argument("--llm-cache", help="LLM 调用缓存文件，用于录制 / 回放")
    parser.add_argument("--baseline", help="基线 summary.json 路径")
    parser.add_argument(
        "--save-baseline", action="store_true", help="把本次结果保存为基线"
    )
    parser.add_argument("--max-accuracy-drop", type=float, default=0.02)
    parser.add_argument("--out", default=str(REPORT_DIR))
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
