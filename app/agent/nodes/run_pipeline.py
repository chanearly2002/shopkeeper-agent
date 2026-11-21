"""
单轮流水线调用节点

用改写后的独立问题和会话的“今天”调用现有单轮问数图（app/agent/graph.py），
把单轮图各节点的进度和结果事件原样转发给外层流。
单轮图编译时关闭了 checkpointer：它的中间状态（召回结果、表结构上下文等）体积大、
只属于本轮，不进入会话检查点；这里只把 SQL、结果预览和错误带回会话状态

单轮流程出错时不向外抛：发一条 error 事件、把错误记进本轮，会话照常进入 remember，
这样用户下一句“换成按月看”仍然能承接这一轮的问题
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState, SessionState
from app.conf.app_config import app_config
from app.core.conversation import summarize_result
from app.core.log import logger


async def run_pipeline(state: SessionState, runtime: Runtime[DataAgentContext]):
    """以独立问题执行一次单轮问数，返回本轮的 SQL、结果预览和错误"""

    writer = runtime.stream_writer
    pipeline_input = DataAgentState(query=state["standalone"], today=state["today"])
    final: dict = {}
    try:
        # custom 是节点进度和结果事件，values 用来拿到单轮图最后的状态
        async for mode, chunk in graph.astream(
            input=pipeline_input,
            context=runtime.context,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                writer(chunk)
            else:
                final = chunk
    except Exception as e:
        error = str(e)
        logger.error(f"单轮问数失败：{error}")
        writer({"type": "error", "message": error})
        return {"sql": None, "result_preview": None, "error": error}

    preview = summarize_result(
        final.get("result") or [],
        bool(final.get("truncated")),
        app_config.conversation.result_preview_rows,
    )
    return {"sql": final.get("sql"), "result_preview": preview, "error": None}
