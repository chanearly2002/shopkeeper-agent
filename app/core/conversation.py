"""
多轮问数的纯函数工具

不依赖项目配置和外部服务，方便单元测试：
- 意图常量：改写节点输出的意图取值，评测集的 gold_intent 也用这些值
- summarize_result：把一轮的查询结果压缩成“列名 + 行数 + 前几行”的预览，供下一轮改写引用
- format_history：把最近几轮历史整理成提示词里的 YAML
- parse_rewrite：校验和兜底改写节点的 LLM 输出
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import yaml

# 与上文无关或已经完整的问题，原样进入单轮流程
INTENT_NEW = "new"
# 依赖上文的追问：省略、追加条件、换维度、纠正口径、引用上一轮结果里的实体继续查
INTENT_FOLLOWUP = "followup"
# 针对上一轮结果本身的提问（哪个最高、差多少）；目前仍改写成独立问题重新查库，
# 后续由 answer_from_result 直接基于上一轮结果回答
INTENT_RESULT_QA = "result_qa"
INTENTS = (INTENT_NEW, INTENT_FOLLOWUP, INTENT_RESULT_QA)


def to_plain(value: Any) -> Any:
    """把数据库返回的 Decimal、日期等值转成 JSON / YAML 友好的基础类型"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # 整数金额保持整数，避免预览里出现 100.0 这种多余的小数位
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def summarize_result(rows: list[dict], truncated: bool, max_rows: int = 5) -> dict:
    """
    把查询结果压缩成预览：列名、行数、是否截断、前 max_rows 行

    只保留少量行是为了控制下一轮提示词的 token；结果是纯基础类型，可以直接写进 checkpointer
    """
    columns = list(rows[0].keys()) if rows else []
    return {
        "columns": columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": [{k: to_plain(v) for k, v in row.items()} for row in rows[:max_rows]],
    }


def format_history(history: list[dict], max_turns: int = 3) -> str:
    """
    把最近 max_turns 轮历史整理成提示词里的 YAML

    只给改写需要的信息：改写后的独立问题、最终 SQL、结果预览；用户原话已经体现在独立问题里。
    越靠后的轮次越新，失败的轮次只给出错误，避免模型引用不存在的结果
    """
    recent = history[-max_turns:] if max_turns > 0 else []
    start = len(history) - len(recent) + 1
    items = []
    for i, turn in enumerate(recent, start=start):
        item: dict[str, Any] = {
            "轮次": i,
            "问题": turn.get("standalone") or turn["question"],
        }
        if turn.get("error"):
            item["结果"] = f"查询失败：{turn['error']}"
        else:
            if turn.get("sql"):
                # 去掉行尾空白，否则 YAML 无法用块样式输出多行 SQL
                item["SQL"] = _Block(
                    "\n".join(
                        line.rstrip() for line in turn["sql"].strip().splitlines()
                    )
                )
            preview = turn.get("result_preview")
            if preview:
                item["结果"] = {
                    "列": preview["columns"],
                    "行数": preview["row_count"],
                    "前几行": preview["rows"],
                }
        items.append(item)
    return yaml.dump(items, Dumper=_HistoryDumper, allow_unicode=True, sort_keys=False)


class _Block(str):
    """多行文本标记：在提示词 YAML 里用 | 块样式输出，保留换行，便于模型阅读 SQL"""


class _HistoryDumper(yaml.SafeDumper):
    """输出历史 YAML 用的 Dumper，只多认识 _Block 一种类型"""


_HistoryDumper.add_representer(
    _Block,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(data), style="|" if "\n" in data else None
    ),
)


def parse_rewrite(output: Any, user_input: str) -> tuple[str, str]:
    """
    校验改写节点的 LLM 输出，返回 (intent, standalone_question)

    意图不在取值范围内时按追问处理（有上文时追问是更安全的假设）；
    改写结果为空时退回用户原话，保证下游始终拿到一个可用的问题
    """
    if not isinstance(output, dict):
        raise ValueError(f"改写结果不是 JSON 对象：{output!r}")
    intent = str(output.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        intent = INTENT_FOLLOWUP
    standalone = str(output.get("standalone_question") or "").strip()
    if intent == INTENT_NEW or not standalone:
        # 新问题必须原样保留，防止模型把上文条件“好心”带进来
        standalone = user_input.strip()
    return intent, standalone
