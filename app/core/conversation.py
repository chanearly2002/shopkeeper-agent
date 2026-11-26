"""
多轮问数的纯函数工具

不依赖项目配置和外部服务，方便单元测试：
- 意图常量：改写节点输出的意图取值，评测集的 gold_intent 也用这些值
- summarize_result：把一轮的查询结果压缩成“列名 + 行数 + 前几行”的预览，供下一轮改写引用
- format_history：把最近几轮历史整理成提示词里的 YAML
- parse_rewrite：校验和兜底改写节点的 LLM 输出（含澄清问题和选项）
- rows_for_answer / parse_answer：基于上一轮结果直接回答时的输入裁剪和输出校验
- format_reference / merge_clarification：追问时的参考 SQL、澄清回答的拼接
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import yaml

# 与上文无关或已经完整的问题，原样进入单轮流程
INTENT_NEW = "new"
# 依赖上文的追问：省略、追加条件、换维度、纠正口径、引用上一轮结果里的实体继续查
INTENT_FOLLOWUP = "followup"
# 针对上一轮结果本身的提问（哪个最高、差多少）；上一轮结果不大时由 answer_from_result
# 直接基于结果回答，否则改写成独立问题重新查库
INTENT_RESULT_QA = "result_qa"
# 信息不足、存在多种理解且结果会明显不同时，先反问用户（interrupt），拿到回答后再改写
INTENT_CLARIFY = "clarify"
INTENTS = (INTENT_NEW, INTENT_FOLLOWUP, INTENT_RESULT_QA, INTENT_CLARIFY)
# 澄清问题最多给几个选项，前端按钮放得下
MAX_CLARIFY_OPTIONS = 4


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
        elif turn.get("answer"):
            # 基于上一轮结果直接回答的轮次没有 SQL，把回答给下一轮参考
            item["回答"] = turn["answer"]
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


@dataclass
class Rewrite:
    """改写节点的输出：意图、独立问题，以及意图为 clarify 时的澄清问题和选项"""

    intent: str
    standalone: str
    clarify_question: str | None = None
    options: list[str] = field(default_factory=list)


def parse_rewrite(output: Any, user_input: str, allow_clarify: bool = True) -> Rewrite:
    """
    校验改写节点的 LLM 输出

    意图不在取值范围内时按追问处理（有上文时追问是更安全的假设）；
    改写结果为空时退回用户原话，保证下游始终拿到一个可用的问题；
    不允许澄清（已经澄清过一次或功能关闭）或没给出澄清问题时，clarify 降级为追问
    """
    if not isinstance(output, dict):
        raise ValueError(f"改写结果不是 JSON 对象：{output!r}")
    intent = str(output.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        intent = INTENT_FOLLOWUP
    standalone = str(output.get("standalone_question") or "").strip()
    question = str(output.get("clarify_question") or "").strip()
    if intent == INTENT_CLARIFY and (not allow_clarify or not question):
        intent = INTENT_FOLLOWUP
    if intent == INTENT_NEW or not standalone:
        # 新问题必须原样保留，防止模型把上文条件“好心”带进来
        standalone = user_input.strip()
    if intent != INTENT_CLARIFY:
        return Rewrite(intent, standalone)
    raw_options = output.get("options") or []
    options = [str(o).strip() for o in raw_options if isinstance(o, (str, int, float))]
    options = list(dict.fromkeys(o for o in options if o))[:MAX_CLARIFY_OPTIONS]
    return Rewrite(intent, standalone, question, options)


def merge_clarification(user_input: str, reply: str) -> str:
    """把用户对澄清问题的回答拼到原话后面，交给改写节点重新理解"""
    return f"{user_input.strip()}（补充：{reply.strip()}）"


def format_reference(question: str | None, sql: str | None) -> str:
    """
    追问时给 SQL 生成节点的参考：上一轮的问题和 SQL；没有时返回空串

    返回值自带前后换行，模板里放在示例和任务要求之间：为空时提示词与单轮完全相同，
    单轮查询的提示词和 LLM 缓存都不受影响
    """
    if not sql:
        return ""
    return (
        "\n上一轮的问题和 SQL（本轮是在它基础上的追问：表关联方式、口径和过滤条件优先沿用，"
        "只按本轮问题做必要修改；与本轮问题无关的条件不要保留）：\n"
        f"问题：{question or ''}\n{sql.strip()}\n"
    )


def rows_for_answer(
    rows: list[dict], truncated: bool, max_rows: int
) -> list[dict] | None:
    """
    上一轮结果能否用来直接回答：被截断或超过 max_rows 行时不行（返回 None），
    否则返回转成基础类型的全部行，可以直接写进 checkpointer
    """
    if truncated or not rows or len(rows) > max_rows:
        return None
    return [{k: to_plain(v) for k, v in row.items()} for row in rows]


def parse_answer(output: Any) -> str | None:
    """校验结果问答节点的输出：能回答时返回回答文本，不能回答或格式不对时返回 None"""
    if not isinstance(output, dict) or not output.get("answerable"):
        return None
    answer = str(output.get("answer") or "").strip()
    return answer or None
