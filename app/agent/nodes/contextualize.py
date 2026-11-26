"""
追问理解节点（多轮会话图的入口）

借鉴 Snowflake Cortex Analyst 的多轮方案：先把用户的追问改写成独立完整的问题，
后面的单轮问数流水线完全不动。同时判断意图（new / followup / result_qa / clarify），
并把改写结果作为 rewrite 事件推给前端展示“我理解为：……”

- 首轮没有上文，默认跳过 LLM 调用，单轮问数的成本和效果不受影响
  （conversation.contextualize_first_turn 打开后，首轮也会改写，从而能对首轮问题反问）
- clarify：信息不足时由 ask_clarification 反问用户，用户回答后带着“（补充：……）”回到这里重新改写；
  每轮最多反问一次
- 每轮开头清空“本轮”字段，保证后面任何分支都不会读到上一轮的 SQL、回答或反问
"""

from datetime import date

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import SessionState
from app.conf.app_config import app_config
from app.core.conversation import (
    INTENT_CLARIFY,
    INTENT_NEW,
    Rewrite,
    format_history,
    merge_clarification,
    parse_rewrite,
)
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


def resolve_today(state: SessionState) -> str:
    """确定本轮的“今天”：请求或会话里已有的 > agent.today 配置 > 真实日期"""
    return state.get("today") or app_config.agent.today or date.today().isoformat()


async def contextualize(state: SessionState, runtime: Runtime[DataAgentContext]):
    """把本轮输入改写成独立问题并判断意图"""

    writer = runtime.stream_writer
    step = "理解问题"
    writer({"type": "progress", "step": step, "status": "running"})

    history = state.get("history") or []
    clarified = bool(state.get("clarified"))
    # 用户回答过反问时，把回答拼在原话后面一起交给模型改写
    user_input = state["user_input"].strip()
    if clarified and state.get("clarification"):
        user_input = merge_clarification(user_input, state["clarification"])
    conf = app_config.conversation
    # 反问过一次就不再反问；功能关闭时也不反问
    allow_clarify = conf.enable_clarify and not clarified

    if not history and not clarified and not conf.contextualize_first_turn:
        # 首轮没有上文可依赖，原样作为新问题
        rewrite = Rewrite(INTENT_NEW, user_input)
        writer({"type": "progress", "step": step, "status": "success"})
    else:
        try:
            prompt = PromptTemplate(
                template=load_prompt("contextualize_question"),
                input_variables=["history", "question"],
            )
            chain = prompt | llm | JsonOutputParser()
            output = await chain.ainvoke(
                {
                    "history": format_history(history, conf.history_turns)
                    if history
                    else "无",
                    "question": user_input,
                }
            )
            rewrite = parse_rewrite(output, user_input, allow_clarify)
            writer({"type": "progress", "step": step, "status": "success"})
        except Exception as e:
            # 改写只是锦上添花：失败时退回“按原话当作新问题”，不让整轮问数中断
            logger.error(f"{step} failed，按原问题处理：{e}")
            rewrite = Rewrite(INTENT_NEW, user_input)
            writer(
                {
                    "type": "progress",
                    "step": step,
                    "status": "error",
                    "detail": f"改写失败，按原问题查询：{e}",
                }
            )

    logger.info(f"本轮意图：{rewrite.intent}，改写后的问题：{rewrite.standalone}")
    if rewrite.intent != INTENT_CLARIFY:
        # 要反问时还没有确定的问题，等用户回答后再发 rewrite
        writer(
            {
                "type": "rewrite",
                "standalone_question": rewrite.standalone,
                "intent": rewrite.intent,
            }
        )
    return {
        "intent": rewrite.intent,
        "standalone": rewrite.standalone,
        "clarify_question": rewrite.clarify_question,
        "clarify_options": rewrite.options,
        "today": resolve_today(state),
        # 清空本轮的输出字段，后面的分支各自写入
        "sql": None,
        "result_preview": None,
        "error": None,
        "answer": None,
    }
