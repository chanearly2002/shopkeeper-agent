"""
评测集静态检查

不连数据库，只检查评测题本身：字段齐全、id 唯一、每类 5 题、expect 与标准 SQL 一致、
SQL 能通过只读校验。标准 SQL 与错误写法在数仓上的执行检查见 evals/check_dataset.py
"""

from evals.dataset import (
    DEFAULT_DATASET,
    MULTITURN_DATASET,
    check_leakage,
    check_structure,
    load_dataset,
    load_examples,
)


def test_trap_dataset_is_well_formed():
    items = load_dataset(DEFAULT_DATASET)
    assert len(items) == 50
    assert check_structure(items, per_category=5) == []


def test_multiturn_dataset_is_well_formed():
    items = load_dataset(MULTITURN_DATASET)
    assert len(items) == 20
    assert all(len(item["turns"]) >= 2 for item in items)
    assert check_structure(items, per_category=None) == []
    assert check_leakage(items, load_examples()) == []


def test_multiturn_intent_checks():
    base = {
        "gold_sql": "select 1 from fact_order",
        "trap_sql": "select 2 from fact_order",
        "trap": "t",
        "expect": {"tables": ["fact_order"]},
        "order_matters": False,
    }
    items = [
        {
            "id": "m",
            "category": "c",
            "difficulty": "easy",
            "today": "2025-12-31",
            "turns": [
                {**base, "question": "q1", "gold_intent": "followup"},
                {**base, "question": "q2"},
                {
                    **base,
                    "question": "q3",
                    "gold_intent": "clarify",
                    "gold_standalone": "x",
                },
                {
                    **base,
                    "question": "q4",
                    "gold_intent": "new",
                    "gold_standalone": "q4 改了",
                },
            ],
        }
    ]
    errors = check_structure(items, per_category=None)
    assert any("第 1 轮: 首轮没有上文" in e for e in errors)
    assert any("第 2 轮: 追问轮缺少字段 gold_intent" in e for e in errors)
    assert any("第 2 轮: 追问轮缺少字段 gold_standalone" in e for e in errors)
    assert any("第 3 轮: gold_intent 必须是" in e for e in errors)
    assert any("第 4 轮: 新话题的 gold_standalone" in e for e in errors)


def test_check_structure_catches_problems():
    items = [
        {
            "id": "x",
            "category": "c",
            "difficulty": "easy",
            "today": "2025-12-31",
            "turns": [
                {
                    "question": "q",
                    "gold_sql": "select 1 from fact_order",
                    "trap_sql": "drop table fact_order",
                    "trap": "t",
                    "expect": {"tables": ["dim_user"], "metrics": ["不存在的指标"]},
                    "order_matters": False,
                }
            ],
        }
    ]
    errors = check_structure(items + items, per_category=None)
    assert any("id 重复" in e for e in errors)
    assert any("dim_user" in e for e in errors)
    assert any("不存在的指标" in e for e in errors)
    assert any("trap_sql 未通过只读校验" in e for e in errors)


def test_examples_do_not_leak_into_eval_set():
    assert check_leakage(load_dataset(DEFAULT_DATASET), load_examples()) == []


def test_leakage_detected():
    items = [
        {
            "id": "x",
            "turns": [{"question": "2025 年的销售额是多少？", "gold_sql": "select 1"}],
        }
    ]
    examples = [{"question": "2025年的销售额是多少", "sql": "select 2 from t"}]
    assert check_leakage(items, examples)
