"""
会话记忆节点

把本轮的原话、改写后的问题、意图、SQL、结果预览和“今天”追加到会话历史，
由外层会话图的 checkpointer 按 thread_id 持久化，供下一轮改写引用
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import SessionState, Turn


async def remember(state: SessionState, runtime: Runtime[DataAgentContext]):
    """追加本轮记录；history 的 reducer 是列表拼接，所以只返回新的一轮"""

    turn = Turn(
        question=state["user_input"],
        standalone=state["standalone"],
        intent=state["intent"],
        sql=state.get("sql"),
        result_preview=state.get("result_preview"),
        error=state.get("error"),
        today=state["today"],
    )
    return {"history": [turn]}
