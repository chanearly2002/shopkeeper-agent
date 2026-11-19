"""
评测集静态检查

不连数据库，只检查评测题本身：字段齐全、id 唯一、每类 5 题、expect 与标准 SQL 一致、
SQL 能通过只读校验。标准 SQL 与错误写法在数仓上的执行检查见 evals/check_dataset.py
"""

from evals.dataset import DEFAULT_DATASET, check_structure, load_dataset


def test_trap_dataset_is_well_formed():
    items = load_dataset(DEFAULT_DATASET)
    assert len(items) == 50
    assert check_structure(items, per_category=5) == []


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
