"""
业务术语向量仓储

术语数量很少，问数时通过 scroll_all 取出全部术语做名称 / 别名的精确匹配；
同时按名称和别名写入向量，留给以后做语义匹配
"""

from qdrant_client import AsyncQdrantClient

from app.conf.app_config import app_config
from app.entities.term_info import TermInfo
from app.repositories.qdrant.base_qdrant_repository import BaseQdrantRepository


class TermQdrantRepository(BaseQdrantRepository[TermInfo]):
    """负责业务术语集合的写入和读取"""

    entity_cls = TermInfo

    def __init__(self, client: AsyncQdrantClient):
        super().__init__(client, app_config.knowledge.term_collection)
