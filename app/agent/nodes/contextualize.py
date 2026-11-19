"""
追问理解节点（多轮会话图的入口）

借鉴 Snowflake Cortex Analyst 的多轮方案：先把用户的追问改写成独立完整的问题，
后面的单轮问数流水线完全不动。同时判断意图（new / followup / result_qa），
并把改写结果作为 rewrite 事件推给前端展示“我理解为：……”
首轮没有上文，直接跳过 LLM 调用，单轮问数的成本和效果不受影响
"""

from datetime import date

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import SessionState
from app.conf.app_config import app_config
from app.core.conversation import INTENT_NEW, format_history, parse_rewrite
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

    user_input = state["user_input"].strip()
    history = state.get("history") or []
    today = resolve_today(state)

    if not history:
        # 首轮没有上文可依赖，原样作为新问题
        intent, standalone = INTENT_NEW, user_input
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
                    "history": format_history(
                        history, app_config.conversation.history_turns
                    ),
                    "question": user_input,
                }
            )
            intent, standalone = parse_rewrite(output, user_input)
            writer({"type": "progress", "step": step, "status": "success"})
        except Exception as e:
            # 改写只是锦上添花：失败时退回“按原话当作新问题”，不让整轮问数中断
            logger.error(f"{step} failed，按原问题处理：{e}")
            intent, standalone = INTENT_NEW, user_input
            writer(
                {
                    "type": "progress",
                    "step": step,
                    "status": "error",
                    "detail": f"改写失败，按原问题查询：{e}",
                }
            )

    logger.info(f"本轮意图：{intent}，改写后的问题：{standalone}")
    writer({"type": "rewrite", "standalone_question": standalone, "intent": intent})
    return {"intent": intent, "standalone": standalone, "today": today}
