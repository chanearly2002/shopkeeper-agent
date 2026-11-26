"""
多轮问数会话图

外层会话图 + 内层单轮流水线（方案见 docs/design/borrowing-and-roadmap.md 第四节）：

    START ─ contextualize ─┬─(clarify)──────────── ask_clarification ─(interrupt / resume)─► contextualize
                           ├─(result_qa 且有上轮结果)─ answer_from_result ─┬─(答出)──────────────► remember ─ END
                           │                                              └─(答不了)─► run_pipeline ─┘
                           └─(new / followup / 其他)─────────────────────────────────► run_pipeline ─► remember

- contextualize：把追问改写成独立问题并判断意图（首轮默认跳过 LLM）
- ask_clarification：信息不足时 interrupt() 反问用户，回答后回到 contextualize（每轮最多一次）
- answer_from_result：针对上一轮结果的提问，结果不大时直接回答、不查库；答不了就退回查库
- run_pipeline：以普通调用方式运行现有单轮问数图，追问时带上上一轮 SQL 作参考
- remember：把本轮问题、改写、SQL、结果预览或回答追加到会话历史

checkpointer 只挂在这一层，按 thread_id 保存会话历史，也是 interrupt 暂停与恢复的前提；
单轮图编译时关闭了 checkpointer，中间状态不进检查点。当前用进程内的 InMemorySaver，服务重启后会话丢失
"""

import asyncio
import uuid

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.answer_from_result import answer_from_result
from app.agent.nodes.ask_clarification import ask_clarification
from app.agent.nodes.contextualize import contextualize
from app.agent.nodes.remember import remember
from app.agent.nodes.run_pipeline import run_pipeline
from app.agent.state import SessionState
from app.core.conversation import INTENT_CLARIFY, INTENT_RESULT_QA


def route_after_contextualize(state: SessionState) -> str:
    """按意图分流：反问、基于上一轮结果回答，或者查库"""
    if state["intent"] == INTENT_CLARIFY:
        return "ask_clarification"
    if state["intent"] == INTENT_RESULT_QA and state.get("last_result"):
        return "answer_from_result"
    return "run_pipeline"


def route_after_answer(state: SessionState) -> str:
    """直接回答成功就结束本轮，答不了就退回查库"""
    return "remember" if state.get("answer") else "run_pipeline"


def build_session_graph(checkpointer: BaseCheckpointSaver):
    """用给定的 checkpointer 编译会话图；评测 runner 可以传入自己的 checkpointer"""
    builder = StateGraph(state_schema=SessionState, context_schema=DataAgentContext)
    builder.add_node("contextualize", contextualize)
    builder.add_node("ask_clarification", ask_clarification)
    builder.add_node("answer_from_result", answer_from_result)
    builder.add_node("run_pipeline", run_pipeline)
    builder.add_node("remember", remember)
    builder.add_edge(START, "contextualize")
    builder.add_conditional_edges(
        "contextualize",
        route_after_contextualize,
        ["ask_clarification", "answer_from_result", "run_pipeline"],
    )
    # 用户回答反问后回到改写节点，结合回答重新理解问题
    builder.add_edge("ask_clarification", "contextualize")
    builder.add_conditional_edges(
        "answer_from_result", route_after_answer, ["remember", "run_pipeline"]
    )
    builder.add_edge("run_pipeline", "remember")
    builder.add_edge("remember", END)
    return builder.compile(checkpointer=checkpointer)


# 应用级会话图：进程内保存所有会话，HTTP 接口按请求里的 thread_id 取用
session_graph = build_session_graph(InMemorySaver())

if __name__ == "__main__":

    async def test():
        """本地调试：不经 HTTP 在同一个会话里连续问两轮，打印流式事件"""
        from app.clients.embedding_client_manager import embedding_client_manager
        from app.clients.es_client_manager import es_client_manager
        from app.clients.mysql_client_manager import (
            dw_mysql_client_manager,
            meta_mysql_client_manager,
        )
        from app.clients.qdrant_client_manager import qdrant_client_manager
        from app.services.query_service import QueryService

        qdrant_client_manager.init()
        embedding_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        thread_id = uuid.uuid4().hex
        for question in [
            "2025 年各大区的支付金额，按从高到低排序",
            "第一名的按月拆开看",
        ]:
            # 每轮像 HTTP 请求一样使用新的 Session，会话历史由 checkpointer 承接
            async with (
                meta_mysql_client_manager.session_factory() as meta_session,
                dw_mysql_client_manager.session_factory() as dw_session,
            ):
                service = QueryService.from_sessions(meta_session, dw_session)
                async for chunk in service.query(question, thread_id=thread_id):
                    print(chunk, end="")

        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    asyncio.run(test())
