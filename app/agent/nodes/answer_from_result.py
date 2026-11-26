"""
基于上一轮结果回答节点

用户针对上一轮结果本身提问（result_qa，如“哪个最高”“占比多少”），而上一轮结果不大
（不超过 conversation.answer_max_rows 行、没有被截断）时，直接把结果交给 LLM 回答，不再查库，
这补上了 Snowflake Cortex Analyst“拿不到上一轮结果”的限制

模型判断结果不足以回答、输出格式不对或调用失败时都不报错：返回 answer=None，
由会话图退回 run_pipeline，用改写后的独立问题重新查库
"""

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import SessionState
from app.core.conversation import parse_answer
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def answer_from_result(state: SessionState, runtime: Runtime[DataAgentContext]):
    """用上一轮的完整结果直接回答本轮问题"""

    writer = runtime.stream_writer
    step = "基于结果回答"
    writer({"type": "progress", "step": step, "status": "running"})

    # last_result 来自最近一次查库，对应历史里最近一条有 SQL 的轮次
    previous = next(
        (t for t in reversed(state.get("history") or []) if t.get("sql")), {}
    )
    try:
        prompt = PromptTemplate(
            template=load_prompt("answer_from_result"),
            input_variables=["question", "previous_question", "previous_sql", "rows"],
        )
        chain = prompt | llm | JsonOutputParser()
        output = await chain.ainvoke(
            {
                "question": state["standalone"],
                "previous_question": previous.get("standalone") or "",
                "previous_sql": (previous.get("sql") or "").strip(),
                "rows": yaml.dump(
                    state["last_result"], allow_unicode=True, sort_keys=False
                ),
            }
        )
        answer = parse_answer(output)
    except Exception as e:
        logger.error(f"{step} failed，改为查库：{e}")
        answer = None

    if answer is None:
        logger.info("上一轮结果不足以回答，改为查库")
        writer(
            {
                "type": "progress",
                "step": step,
                "status": "error",
                "detail": "上一轮结果不足以回答，改为查询数据库",
            }
        )
        return {"answer": None}

    logger.info(f"基于上一轮结果回答：{answer}")
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "answer", "text": answer})
    return {"answer": answer}
