"""
反问澄清节点

改写节点判断信息不足（clarify）时，用 LangGraph 的 interrupt() 暂停会话图，
把问题和候选项交给调用方（QueryService 转成 SSE 的 clarify 事件）。用户回答后，
调用方以 Command(resume=回答) 恢复：interrupt() 返回用户的回答，本节点把它记进 clarification，
回到 contextualize 结合原话重新改写。interrupt 依赖 checkpointer，所以只能放在会话图里

恢复时 LangGraph 会从头重新执行本节点，interrupt() 之前的代码会跑两遍；两次分别属于
“提问”和“回答”两个请求的事件流，各自的前端消息里只出现一次，所以 running 事件可以放在前面
"""

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.context import DataAgentContext
from app.agent.state import SessionState
from app.core.log import logger


async def ask_clarification(state: SessionState, runtime: Runtime[DataAgentContext]):
    """暂停等待用户回答反问，拿到回答后交回改写节点"""

    writer = runtime.stream_writer
    step = "反问澄清"
    writer({"type": "progress", "step": step, "status": "running"})
    reply = interrupt(
        {
            "question": state["clarify_question"],
            "options": state.get("clarify_options") or [],
        }
    )
    reply = str(reply or "").strip() or "按你的理解直接查询"
    logger.info(f"用户回答了反问：{reply}")
    writer({"type": "progress", "step": step, "status": "success", "detail": reply})
    return {
        "clarification": reply,
        # 已经反问过，回到 contextualize 后不再反问
        "clarified": True,
    }
