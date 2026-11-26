"""
SQL 生成节点

负责根据用户问题和前面整理出的表结构 指标 日期 数据库环境生成候选 SQL。
本节点只生成 SQL，不做校验和执行，后续会交给 validate_sql 和 run_sql 继续处理。
"""

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.nodes.recall_knowledge import format_examples, format_terms
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.sql_safety import clean_sql
from app.prompt.prompt_loader import load_prompt


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """基于已检索和过滤的上下文生成 SQL"""

    writer = runtime.stream_writer
    step = "生成SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这些上下文都由前置节点准备完成，模型只在给定表 字段 指标口径范围内生成 SQL
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]
        query = state["query"]

        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=[
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
                "query",
                "terms",
                "examples",
                "reference",
            ],
        )
        # SQL 生成节点只需要纯文本 SQL，不能要求模型输出 JSON 或 Markdown 代码块
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser

        raw_sql = await chain.ainvoke(
            {
                # YAML 更适合放进提示词：保留嵌套结构 顺序和中文说明，方便模型理解表字段关系
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "query": query,
                "terms": format_terms(state.get("term_infos", [])),
                "examples": format_examples(state.get("example_infos", [])),
                # 多轮追问时是上一轮的问题和 SQL；单轮为空串，提示词与原来逐字节相同
                "reference": state.get("reference", ""),
            }
        )
        # 模型偶尔仍会输出 ```sql 代码块或末尾分号，统一清洗成可执行的纯 SQL
        sql = clean_sql(raw_sql)
        logger.info(f"生成的SQL：{sql}")
        writer({"type": "progress", "step": step, "status": "success"})
        # 每次生成新 SQL 都重置修正计数
        return {"sql": sql, "correct_attempts": 0}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
