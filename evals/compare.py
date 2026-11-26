"""
查询结果等价判定

判断模型生成的 SQL 与标准 SQL 的执行结果是否“业务上相同”，规则参照 Spider 2.0 的宽松口径：
1. 忽略列名和列顺序：标准答案的每一列都要在预测结果里找到一列内容相同的列，预测结果允许多出列
2. 行：order_matters 为 False 时按多重集合比较，为 True 时按顺序比较
3. 数值有容差：相对误差 1e-9 以内视为相等（只用来吸收浮点误差；金额可达数百万，更宽的容差会放过几元甚至几百元的差异）；预测值做了四舍五入（保留 1~4 位小数，
   或金额较大时取整）也视为相等；同一列允许整体是标准答案的 100 倍（比率 vs 百分数）
4. 日期统一转成 ISO 字符串，Decimal 和整数统一转成 float，字符串去掉首尾空白
5. 时间标签换算：一列预测值整体是“年月”标签（'2025-01'、202501、'2025年1月'）且只涉及一个年份时，
   可以按月份数字与标准列比较；日期 '2025-12-25' 与日期键 20251225 互相认可。
   标准 SQL 常按 dim_date.month 输出 1~12，模型常输出带年份的月份，两者业务上相同

本模块是纯函数，不依赖数据库和项目配置，方便单元测试
"""

import re
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


_YEAR_MONTH = re.compile(r"^(\d{4})\s*[-/.年]?\s*(\d{1,2})\s*月?$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _year_month(value: Any) -> tuple[int, int] | None:
    """把 202501、'202501'、'2025-01'、'2025年1月' 解析成 (年, 月)，不是年月标签时返回 None"""
    if isinstance(value, float) and value.is_integer() and 190001 <= value <= 299912:
        year, month = divmod(int(value), 100)
    elif isinstance(value, str) and (m := _YEAR_MONTH.match(value)):
        year, month = int(m.group(1)), int(m.group(2))
    else:
        return None
    return (year, month) if 1 <= month <= 12 else None


def _as_date_key(value: Any) -> Any:
    """日期与日期键互转：'2025-12-25' -> 20251225.0，20251225.0 -> '2025-12-25'；其他值返回 None"""
    if isinstance(value, str) and (m := _ISO_DATE.match(value)):
        return float("".join(m.groups()))
    if (
        isinstance(value, float)
        and value.is_integer()
        and 19000101 <= value <= 29991231
    ):
        text = str(int(value))
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
        except ValueError:
            return None
    return None


def column_views(values: list) -> dict[str, list]:
    """
    一列预测值可以参与比较的几种形式：原始值，以及满足条件时换算后的月份、日期

    月份换算要求整列都是年月标签且只有一个年份，否则“1 月”指哪年说不清，不做换算
    """
    views = {"raw": values}
    non_null = [v for v in values if v is not None]
    if not non_null:
        return views
    parsed = [_year_month(v) for v in non_null]
    if all(parsed) and len({p[0] for p in parsed}) == 1:
        views["month"] = [
            None if v is None else float(_year_month(v)[1]) for v in values
        ]
    converted = [_as_date_key(v) for v in non_null]
    if all(c is not None for c in converted):
        views["date"] = [None if v is None else _as_date_key(v) for v in values]
    return views


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
    # 每个预测列可能有多种形式（原始值、换算成月份或日期），键为 (列名, 形式)
    pred_data = {
        (c, view): values
        for c in pred_cols
        for view, values in column_views([normalize(r[c]) for r in pred]).items()
    }

    # 先找出每个标准列可能对应的 ((预测列, 形式), 倍数)
    candidates: list[list[tuple[tuple[str, str], int]]] = []
    for gc in gold_cols:
        options = [
            (key, s)
            for key in pred_data
            for s in SCALES
            if _column_matches(pred_data[key], gold_data[gc], s, order_matters)
        ]
        if not options:
            return False
        candidates.append(options)

    # 再在候选里找一组互不重复（同一预测列只能用一次）、且逐行也能对齐的对应关系
    def search(i: int, used: set[str], chosen: list) -> bool:
        if i == len(gold_cols):
            pred_rows = [
                tuple(pred_data[key][r] for key, _ in chosen) for r in range(len(pred))
            ]
            gold_rows = [
                tuple(gold_data[gc][r] for gc in gold_cols) for r in range(len(gold))
            ]
            return _rows_match(
                pred_rows, gold_rows, tuple(s for _, s in chosen), order_matters
            )
        for key, s in candidates[i]:
            column = key[0]
            if column not in used and search(
                i + 1, used | {column}, chosen + [(key, s)]
            ):
                return True
        return False

    return search(0, set(), [])
