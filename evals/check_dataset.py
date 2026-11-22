"""
评测集自检脚本

在真实数仓上逐题检查评测题本身是否有效，而不是评测模型：
1. 结构：必填字段齐全、id 唯一、每个类别的题数符合预期
2. 一致性：expect.tables 都出现在标准 SQL 中，expect.metrics 都是 meta_config.yaml 里的指标
3. 安全：标准 SQL 和错误写法都能通过项目自己的只读校验
4. 有效性：标准 SQL 能执行且结果非空；错误写法能执行，但结果必须与标准答案不同，
   而且保留两位小数后仍然不同，否则这道题检验不出它声称的陷阱

用法：
    uv run python -m evals.check_dataset                      # 默认检查 evals/datasets/traps.yaml
    uv run python -m evals.check_dataset -d evals/datasets/xxx.yaml --per-category 5
    uv run python -m evals.check_dataset -d evals/datasets/multiturn.yaml --per-category 0   # 多轮题，每一轮都检查
数据生成器或数据规模变化后都应重新运行；任何一题不通过时以非 0 退出
"""

import argparse
import asyncio
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.clients.mysql_client_manager import dw_mysql_client_manager
from evals.compare import results_equal
from evals.dataset import (
    DEFAULT_DATASET,
    check_leakage,
    check_structure,
    load_dataset,
    load_examples,
)


async def run_sql(session, sql: str) -> list[dict]:
    """执行一条 SQL，返回字典列表"""
    result = await session.execute(text(sql))
    return [dict(row) for row in result.mappings().fetchall()]


async def check_results(items: list[dict]) -> tuple[list[str], list[tuple]]:
    """在数仓上执行标准 SQL 与错误写法，确认前者有结果、后者结果不同"""
    errors: list[str] = []
    report: list[tuple] = []
    dw_mysql_client_manager.init()
    try:
        async with dw_mysql_client_manager.session_factory() as session:
            for item in items:
                for t, turn in enumerate(item["turns"]):
                    where = f"{item['id']} 第 {t + 1} 轮"
                    try:
                        gold = await run_sql(session, turn["gold_sql"])
                    except Exception as e:
                        errors.append(f"{where}: 标准 SQL 执行失败：{e}")
                        await session.rollback()
                        continue
                    try:
                        trap = await run_sql(session, turn["trap_sql"])
                    except Exception as e:
                        errors.append(f"{where}: 错误写法执行失败：{e}")
                        await session.rollback()
                        continue

                    order = bool(turn.get("order_matters"))
                    if not gold or all(v is None for row in gold for v in row.values()):
                        errors.append(f"{where}: 标准 SQL 结果为空")
                    if not results_equal(gold, gold, order):
                        errors.append(f"{where}: 判定器认为标准结果与自身不同")
                    trap_same = results_equal(trap, gold, order)
                    if trap_same:
                        errors.append(
                            f"{where}: 错误写法的结果与标准答案相同，测不出该陷阱"
                        )
                    elif results_equal(_rounded(trap), gold, order):
                        # 判定器接受四舍五入后的预测值，模型若对错误结果取两位小数就会被判对
                        errors.append(
                            f"{where}: 错误写法保留两位小数后与标准答案无法区分，区分度太弱"
                        )
                    report.append(
                        (
                            item["id"]
                            if len(item["turns"]) == 1
                            else f"{item['id']}#{t + 1}",
                            len(gold),
                            _preview(gold),
                            _preview(trap),
                            not trap_same,
                        )
                    )
    finally:
        await dw_mysql_client_manager.close()
    return errors, report


def _rounded(rows: list[dict]) -> list[dict]:
    """把结果中的数值保留两位小数，模拟模型对结果做了四舍五入"""
    return [
        {
            k: round(float(v), 2)
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
            else v
            for k, v in row.items()
        }
        for row in rows
    ]


def _preview(rows: list[dict]) -> str:
    """结果摘要：单行单列直接显示值，否则显示行数"""
    if len(rows) == 1 and len(rows[0]) <= 3:
        return ", ".join(
            f"{v:.4f}" if isinstance(v, float) else str(v) for v in rows[0].values()
        )
    return f"{len(rows)} 行"


async def check_examples(examples: list[dict]) -> list[str]:
    """few-shot 示例的 SQL 也必须能执行且有结果，否则会把错误写法教给模型"""
    errors: list[str] = []
    dw_mysql_client_manager.init()
    try:
        async with dw_mysql_client_manager.session_factory() as session:
            for i, example in enumerate(examples, start=1):
                try:
                    rows = await run_sql(session, example["sql"])
                except Exception as e:
                    errors.append(f"第 {i} 条示例 SQL 执行失败：{e}")
                    await session.rollback()
                    continue
                if not rows:
                    errors.append(f"第 {i} 条示例 SQL 结果为空")
    finally:
        await dw_mysql_client_manager.close()
    print(f"few-shot 示例 {len(examples)} 条，已在数仓上执行")
    return errors


async def main(path: Path, per_category: int | None) -> int:
    items = load_dataset(path)
    errors = check_structure(items, per_category)
    examples = load_examples()
    errors += check_leakage(items, examples)
    errors += await check_examples(examples)
    result_errors, report = await check_results(items)
    errors += result_errors

    print(f"{'题目':<24}{'行数':>5}  {'标准答案':<36}{'错误写法':<36}可区分")
    for qid, n, gold, trap, ok in report:
        print(f"{qid:<24}{n:>5}  {gold[:34]:<36}{trap[:34]:<36}{'是' if ok else '否'}")
    categories = Counter(item["category"] for item in items)
    print(f"\n共 {len(items)} 题，{len(categories)} 个类别：{dict(categories)}")
    if errors:
        print(f"\n发现 {len(errors)} 个问题：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="校验评测集本身是否有效")
    parser.add_argument("-d", "--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--per-category", type=int, default=5, help="每个类别的预期题数，0 表示不检查"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dataset, args.per_category or None)))
