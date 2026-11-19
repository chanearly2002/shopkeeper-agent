"""
多轮问数会话图

外层会话图 + 内层单轮流水线（方案见 docs/design/borrowing-and-roadmap.md 第四节）：

    START ─ contextualize ─ run_pipeline ─ remember ─ END

- contextualize：把追问改写成独立问题并判断意图（首轮跳过 LLM）
- run_pipeline：以普通调用方式运行现有单轮问数图，转发它的进度和结果事件
- remember：把本轮问题、改写、SQL、结果预览追加到会话历史

checkpointer 只挂在这一层，按 thread_id 保存会话历史；单轮图编译时关闭了 checkpointer，
中间状态不进检查点。当前用进程内的 InMemorySaver，服务重启后会话丢失，后续换成 MySQL 持久化
result_qa 目前也走单轮流水线重新查库，后续再加 answer_from_result 分支直接基于上一轮结果回答
"""

import asyncio
import uuid

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.contextualize import contextualize
from app.agent.nodes.remember import remember
from app.agent.nodes.run_pipeline import run_pipeline
from app.agent.state import SessionState


def build_session_graph(checkpointer: BaseCheckpointSaver):
    """用给定的 checkpointer 编译会话图；评测 runner 可以传入自己的 checkpointer"""
    builder = StateGraph(state_schema=SessionState, context_schema=DataAgentContext)
    builder.add_node("contextualize", contextualize)
    builder.add_node("run_pipeline", run_pipeline)
    builder.add_node("remember", remember)
    builder.add_edge(START, "contextualize")
    builder.add_edge("contextualize", "run_pipeline")
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
