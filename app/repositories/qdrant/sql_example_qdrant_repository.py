"""
已验证问答示例向量仓储

按示例问题的向量做相似度检索，召回结果作为 SQL 生成的 few-shot 示例
"""

from qdrant_client import AsyncQdrantClient

from app.conf.app_config import app_config
from app.entities.sql_example import SqlExample
from app.repositories.qdrant.base_qdrant_repository import BaseQdrantRepository


class SqlExampleQdrantRepository(BaseQdrantRepository[SqlExample]):
    """负责问答示例集合的写入和检索"""

    entity_cls = SqlExample

    def __init__(self, client: AsyncQdrantClient):
        super().__init__(client, app_config.knowledge.example_collection)
