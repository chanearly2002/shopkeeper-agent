"""
单轮流水线调用节点

用改写后的独立问题和会话的“今天”调用现有单轮问数图（app/agent/graph.py），
把单轮图各节点的进度和结果事件原样转发给外层流。
单轮图编译时关闭了 checkpointer：它的中间状态（召回结果、表结构上下文等）体积大、
只属于本轮，不进入会话检查点；这里只把 SQL、结果预览、不大的完整结果和错误带回会话状态

- 追问（followup）时把上一轮的问题和 SQL 作为参考传给 generate_sql：在上一轮 SQL 上增量修改
  比从头生成更稳定，也更容易保持口径一致
- 单轮流程出错时不向外抛：发一条 error 事件、把错误记进本轮，会话照常进入 remember，
  这样用户下一句“换成按月看”仍然能承接这一轮的问题
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState, SessionState
from app.conf.app_config import app_config
from app.core.conversation import (
    INTENT_FOLLOWUP,
    format_reference,
    rows_for_answer,
    summarize_result,
)
from app.core.log import logger


def build_pipeline_input(state: SessionState) -> DataAgentState:
    """组装单轮图的输入；追问时带上最近一轮查过库的问题和 SQL 作为参考"""
    pipeline_input = DataAgentState(query=state["standalone"], today=state["today"])
    if state.get("intent") == INTENT_FOLLOWUP:
        # 最近一轮可能是直接回答的轮次（没有 SQL），往前找最近一条有 SQL 的
        previous = next(
            (t for t in reversed(state.get("history") or []) if t.get("sql")), None
        )
        if previous:
            pipeline_input["reference"] = format_reference(
                previous.get("standalone"), previous["sql"]
            )
    return pipeline_input


async def run_pipeline(state: SessionState, runtime: Runtime[DataAgentContext]):
    """以独立问题执行一次单轮问数，返回本轮的 SQL、结果预览和错误"""

    writer = runtime.stream_writer
    final: dict = {}
    try:
        # custom 是节点进度和结果事件，values 用来拿到单轮图最后的状态
        async for mode, chunk in graph.astream(
            input=build_pipeline_input(state),
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
        return {
            "sql": None,
            "result_preview": None,
            "error": error,
            "last_result": None,
        }

    rows = final.get("result") or []
    truncated = bool(final.get("truncated"))
    conf = app_config.conversation
    return {
        "sql": final.get("sql"),
        "result_preview": summarize_result(rows, truncated, conf.result_preview_rows),
        "error": None,
        # 结果不大时整份留下，下一轮“哪个最高”可以直接回答、不再查库
        "last_result": rows_for_answer(rows, truncated, conf.answer_max_rows),
    }
