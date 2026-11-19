"""
业务知识召回节点

与字段 指标 取值三路召回并行，召回两类由人维护的确定性知识：
- 业务术语：问题里出现术语名或别名就命中（精确匹配，参考 SQLBot 的术语配置），
  例如“高价值会员”“双11当天”，定义会进入 SQL 生成提示词
- 已验证问答示例：按问题向量召回最相似的几条“问题 - SQL”对作为 few-shot
  （参考 Vanna、Databricks Genie 的 example SQL、Snowflake Cortex Analyst 的 verified queries）
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.entities.sql_example import SqlExample
from app.entities.term_info import TermInfo


def match_terms(query: str, terms: list[TermInfo]) -> list[TermInfo]:
    """问题中出现术语名或任一别名即命中，按术语名去重并保持顺序"""
    hits = []
    for term in terms:
        if any(
            word and word.lower() in query.lower() for word in [term.name, *term.alias]
        ):
            hits.append(term)
    return hits


async def recall_knowledge(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回与问题相关的业务术语和已验证问答示例"""

    writer = runtime.stream_writer
    step = "召回业务知识"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        term_repository = runtime.context["term_qdrant_repository"]
        example_repository = runtime.context["sql_example_qdrant_repository"]

        # 术语只有几十条，全部取出做精确匹配，比向量检索更可控
        term_infos = match_terms(query, await term_repository.scroll_all())

        example_infos: list[SqlExample] = []
        if app_config.knowledge.enable_examples:
            embedding = await runtime.context["embedding_client"].aembed_query(query)
            example_infos = await example_repository.search(
                embedding,
                score_threshold=app_config.knowledge.example_score_threshold,
                limit=app_config.knowledge.example_top_k,
            )

        logger.info(
            f"命中业务术语：{[t.name for t in term_infos]}，"
            f"召回问答示例：{[e.question for e in example_infos]}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"term_infos": term_infos, "example_infos": example_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise


def format_terms(term_infos: list[TermInfo]) -> str:
    """把命中的术语整理成提示词文本，没有命中时返回“无”"""
    if not term_infos:
        return "无"
    return "\n".join(f"- {t.name}：{t.definition}" for t in term_infos)


def format_examples(example_infos: list[SqlExample]) -> str:
    """把召回的问答示例整理成提示词文本，没有召回时返回“无”"""
    if not example_infos:
        return "无"
    return "\n\n".join(
        f"示例 {i}：{e.question}\n{e.sql.strip()}"
        for i, e in enumerate(example_infos, start=1)
    )
