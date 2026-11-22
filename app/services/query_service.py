"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建本轮输入、组装 Runtime Context、按 thread_id 驱动多轮会话图，
消费 session_graph.astream 的流式输出，并统一包装成 SSE 文本返回给路由层。
"""

import json
import uuid

from langchain_huggingface import HuggingFaceEndpointEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import DataAgentContext
from app.agent.session_graph import session_graph
from app.agent.state import SessionState
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.repositories.qdrant.sql_example_qdrant_repository import (
    SqlExampleQdrantRepository,
)
from app.repositories.qdrant.term_qdrant_repository import TermQdrantRepository


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
        term_qdrant_repository: TermQdrantRepository,
        sql_example_qdrant_repository: SqlExampleQdrantRepository,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.term_qdrant_repository = term_qdrant_repository
        self.sql_example_qdrant_repository = sql_example_qdrant_repository

    @classmethod
    def from_sessions(cls, meta_session: AsyncSession, dw_session: AsyncSession):
        """
        用两个数据库 Session 和已初始化的全局客户端组装服务

        供本地调试和评测 runner 使用；HTTP 接口通过 api/dependencies.py 的依赖注入组装
        """
        from app.clients.embedding_client_manager import embedding_client_manager
        from app.clients.es_client_manager import es_client_manager
        from app.clients.qdrant_client_manager import qdrant_client_manager

        qdrant = qdrant_client_manager.client
        return cls(
            meta_mysql_repository=MetaMySQLRepository(meta_session),
            embedding_client=embedding_client_manager.client,
            dw_mysql_repository=DWMySQLRepository(dw_session),
            column_qdrant_repository=ColumnQdrantRepository(qdrant),
            metric_qdrant_repository=MetricQdrantRepository(qdrant),
            value_es_repository=ValueESRepository(es_client_manager.client),
            term_qdrant_repository=TermQdrantRepository(qdrant),
            sql_example_qdrant_repository=SqlExampleQdrantRepository(qdrant),
        )

    def build_context(self) -> DataAgentContext:
        """组装图执行需要的外部依赖，节点通过 runtime.context 读取"""
        return DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            term_qdrant_repository=self.term_qdrant_repository,
            sql_example_qdrant_repository=self.sql_example_qdrant_repository,
        )

    async def query(
        self, query: str, today: str | None = None, thread_id: str | None = None
    ):
        """
        在 thread_id 对应的会话里执行一轮问数，并逐段产出 SSE 消息

        不传 thread_id 时新建会话；第一条消息总是 session 事件，告诉前端本轮所属的会话
        """
        thread_id = thread_id or uuid.uuid4().hex
        yield _sse({"type": "session", "thread_id": thread_id})

        # 本轮输入只有用户原话；today 显式传入时覆盖会话里的“今天”，否则沿用会话或配置
        state = SessionState(user_input=query)
        if today:
            state["today"] = today
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = self.build_context()
        # 会话图的 checkpointer 按 thread_id 读取和保存历史
        config = {"configurable": {"thread_id": thread_id}}
        try:
            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度、改写和结果消息
            async for chunk in session_graph.astream(
                input=state, config=config, context=context, stream_mode="custom"
            ):
                yield _sse(chunk)
        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            yield _sse({"type": "error", "message": str(e)})


def _sse(event: dict) -> str:
    """
    把一条事件包装成 SSE 文本

    SSE 要求每条消息以 data: 开头，并以两个换行符结束；
    ensure_ascii=False 保留中文文案，default=str 兜底处理日期等非 JSON 类型
    """
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
