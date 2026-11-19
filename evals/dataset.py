"""
评测集的加载与静态检查

只做不需要数据库的检查：字段完整性、id 唯一、各类别题数、expect 与标准 SQL 是否一致、
SQL 能否通过只读校验。不导入项目配置和数据库客户端，可以直接在 pytest 里运行；
需要连数仓的有效性检查见 check_dataset.py
"""

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import sqlglot
import yaml
from omegaconf import OmegaConf
from sqlglot import exp

from app.conf.meta_config import MetaConfig
from app.core.sql_safety import ensure_readonly

ROOT = Path(__file__).parents[1]
DEFAULT_DATASET = ROOT / "evals" / "datasets" / "traps.yaml"
EXAMPLES_FILE = ROOT / "conf" / "sql_examples.yaml"
# 示例与评测题的问题文本相似度超过该值视为泄漏
LEAKAGE_THRESHOLD = 0.8
# SQL 的阈值更高：这个数仓里大量查询共用“明细→订单→商品→类目”这类关联，
# 结构相似很正常，只拦截几乎相同、只换了过滤值的模板
SQL_LEAKAGE_THRESHOLD = 0.9
REQUIRED_TURN_FIELDS = (
    "question",
    "gold_sql",
    "trap_sql",
    "trap",
    "expect",
    "order_matters",
)


def load_dataset(path: Path) -> list[dict]:
    """读取评测集 YAML"""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def metric_names() -> set[str]:
    """meta_config.yaml 中定义的全部指标名"""
    context = OmegaConf.load(ROOT / "conf" / "meta_config.yaml")
    config: MetaConfig = OmegaConf.to_object(
        OmegaConf.merge(OmegaConf.structured(MetaConfig), context)
    )
    return {m.name for m in config.metrics or []}


def tables_in_sql(sql: str) -> set[str]:
    """解析 SQL 中引用的全部物理表名"""
    return {t.name for t in sqlglot.parse_one(sql, read="mysql").find_all(exp.Table)}


def check_structure(items: list[dict], per_category: int | None) -> list[str]:
    """检查字段完整性 id 唯一性 各类别题数 以及 expect 与 SQL 的一致性"""
    errors: list[str] = []
    ids = Counter(item.get("id") for item in items)
    errors += [f"id 重复：{i}" for i, n in ids.items() if n > 1]

    if per_category is not None:
        for category, n in Counter(item.get("category") for item in items).items():
            if n != per_category:
                errors.append(f"类别 {category} 有 {n} 题，预期 {per_category} 题")

    metrics = metric_names()
    for item in items:
        prefix = item.get("id", "<缺少 id>")
        for key in ("id", "category", "difficulty", "today", "turns"):
            if key not in item:
                errors.append(f"{prefix}: 缺少字段 {key}")
        for t, turn in enumerate(item.get("turns", [])):
            where = f"{prefix} 第 {t + 1} 轮"
            for key in REQUIRED_TURN_FIELDS:
                if key not in turn:
                    errors.append(f"{where}: 缺少字段 {key}")
            if "gold_sql" not in turn:
                continue
            expect = turn.get("expect") or {}
            missing = set(expect.get("tables", [])) - tables_in_sql(turn["gold_sql"])
            if missing:
                errors.append(
                    f"{where}: expect.tables 中的 {sorted(missing)} 没有出现在标准 SQL 中"
                )
            unknown = set(expect.get("metrics", [])) - metrics
            if unknown:
                errors.append(
                    f"{where}: expect.metrics 中的 {sorted(unknown)} 不是已定义的指标"
                )
            for key in ("gold_sql", "trap_sql"):
                try:
                    ensure_readonly(turn.get(key, ""))
                except ValueError as e:
                    errors.append(f"{where}: {key} 未通过只读校验：{e}")
    return errors


def load_examples(path: Path = EXAMPLES_FILE) -> list[dict]:
    """读取已验证问答示例（few-shot 示例库）"""
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _norm(text: str) -> str:
    """去掉空白和标点后转小写，用于相似度比较"""
    return re.sub(r"[\s，。？！、,.?!;；:：()（）]+", "", text).lower()


def check_leakage(items: list[dict], examples: list[dict]) -> list[str]:
    """
    检查 few-shot 示例是否与评测题过于相似

    示例会在问数时作为参考写法召回，如果和评测题几乎一样，评测分数就会虚高；
    问题（含 paraphrases）和 SQL 都要比较
    """
    errors: list[str] = []
    for item in items:
        for turn in item.get("turns", []):
            questions = [turn.get("question", ""), *turn.get("paraphrases", [])]
            for i, example in enumerate(examples, start=1):
                for q in questions:
                    ratio = SequenceMatcher(
                        None, _norm(q), _norm(example["question"])
                    ).ratio()
                    if ratio >= LEAKAGE_THRESHOLD:
                        errors.append(
                            f"{item['id']}: 问题与第 {i} 条示例过于相似（{ratio:.2f}）：{q} / {example['question']}"
                        )
                sql_ratio = SequenceMatcher(
                    None, _norm(turn.get("gold_sql", "")), _norm(example["sql"])
                ).ratio()
                if sql_ratio >= SQL_LEAKAGE_THRESHOLD:
                    errors.append(
                        f"{item['id']}: 标准 SQL 与第 {i} 条示例 SQL 过于相似（{sql_ratio:.2f}）"
                    )
    return errors
