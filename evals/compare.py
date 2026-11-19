"""
查询结果等价判定

判断模型生成的 SQL 与标准 SQL 的执行结果是否“业务上相同”，规则参照 Spider 2.0 的宽松口径：
1. 忽略列名和列顺序：标准答案的每一列都要在预测结果里找到一列内容相同的列，预测结果允许多出列
2. 行：order_matters 为 False 时按多重集合比较，为 True 时按顺序比较
3. 数值有容差：相对误差 1e-9 以内视为相等（只用来吸收浮点误差；金额可达数百万，更宽的容差会放过几元甚至几百元的差异）；预测值做了四舍五入（保留 1~4 位小数，
   或金额较大时取整）也视为相等；同一列允许整体是标准答案的 100 倍（比率 vs 百分数）
4. 日期统一转成 ISO 字符串，Decimal 和整数统一转成 float，字符串去掉首尾空白

本模块是纯函数，不依赖数据库和项目配置，方便单元测试
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

REL_TOL = 1e-9
# 比率列允许用百分数表示
SCALES = (1, 100)


def normalize(value: Any) -> Any:
    """把一个单元格的值转换成便于比较的形式"""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    # 数据库驱动有时把 DECIMAL 以字符串返回，能转成数字的统一按数字比较
    try:
        return float(text)
    except ValueError:
        return text


def _decimals(x: float) -> int:
    """预测值的小数位数，用来判断它是否是标准值四舍五入后的结果"""
    text = f"{x:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def values_equal(pred: Any, gold: Any, scale: int = 1) -> bool:
    """判断单个预测值与标准值是否相等，scale 表示预测列相对标准列的倍数"""
    if pred is None or gold is None:
        return pred is None and gold is None
    if isinstance(pred, float) and isinstance(gold, float):
        expected = gold * scale
        if abs(pred - expected) <= REL_TOL * max(abs(expected), 1e-9):
            return True
        if abs(pred - expected) < 1e-9:
            return True
        # 预测值做了四舍五入：把标准值按同样的小数位数取整后比较
        digits = _decimals(pred)
        if 1 <= digits <= 4 or (digits == 0 and abs(expected) >= 1000):
            return round(expected, digits) == round(pred, digits)
        return False
    return pred == gold


def _column_matches(
    pred_col: list, gold_col: list, scale: int, order_matters: bool
) -> bool:
    """一列预测值能否与一列标准值对应（只比较列内容，不考虑与其他列的行对齐）"""
    if len(pred_col) != len(gold_col):
        return False
    if order_matters:
        return all(values_equal(p, g, scale) for p, g in zip(pred_col, gold_col))
    remaining = list(pred_col)
    for g in gold_col:
        for i, p in enumerate(remaining):
            if values_equal(p, g, scale):
                remaining.pop(i)
                break
        else:
            return False
    return True


def _rows_match(
    pred_rows: list[tuple],
    gold_rows: list[tuple],
    scales: tuple[int, ...],
    order_matters: bool,
) -> bool:
    """按已确定的列对应关系逐行比较"""
    if order_matters:
        return all(
            all(values_equal(p, g, s) for p, g, s in zip(pr, gr, scales))
            for pr, gr in zip(pred_rows, gold_rows)
        )
    remaining = list(pred_rows)
    for gr in gold_rows:
        for i, pr in enumerate(remaining):
            if all(values_equal(p, g, s) for p, g, s in zip(pr, gr, scales)):
                remaining.pop(i)
                break
        else:
            return False
    return True


def results_equal(
    pred: list[dict], gold: list[dict], order_matters: bool = False
) -> bool:
    """判断预测结果与标准结果是否等价，两者都是“列名 -> 值”的字典列表"""
    if len(pred) != len(gold):
        return False
    if not gold:
        return True

    gold_cols = list(gold[0].keys())
    pred_cols = list(pred[0].keys())
    if len(pred_cols) < len(gold_cols):
        return False

    gold_data = {c: [normalize(r[c]) for r in gold] for c in gold_cols}
    pred_data = {c: [normalize(r[c]) for r in pred] for c in pred_cols}

    # 先找出每个标准列可能对应的 (预测列, 倍数)
    candidates: list[list[tuple[str, int]]] = []
    for gc in gold_cols:
        options = [
            (pc, s)
            for pc in pred_cols
            for s in SCALES
            if _column_matches(pred_data[pc], gold_data[gc], s, order_matters)
        ]
        if not options:
            return False
        candidates.append(options)

    # 再在候选里找一组互不重复、且逐行也能对齐的对应关系
    def search(i: int, used: set[str], chosen: list[tuple[str, int]]) -> bool:
        if i == len(gold_cols):
            pred_rows = [
                tuple(pred_data[pc][r] for pc, _ in chosen) for r in range(len(pred))
            ]
            gold_rows = [
                tuple(gold_data[gc][r] for gc in gold_cols) for r in range(len(gold))
            ]
            return _rows_match(
                pred_rows, gold_rows, tuple(s for _, s in chosen), order_matters
            )
        for pc, s in candidates[i]:
            if pc not in used and search(i + 1, used | {pc}, chosen + [(pc, s)]):
                return True
        return False

    return search(0, set(), [])
