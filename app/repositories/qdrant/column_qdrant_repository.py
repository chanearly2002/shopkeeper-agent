"""
字段向量仓储

管理字段向量集合 column_info_collection（集合名来自配置）
Service 层负责决定一个字段要拆成哪些 point，读写细节由 BaseQdrantRepository 统一实现
"""

from qdrant_client import AsyncQdrantClient

from app.conf.app_config import app_config
from app.entities.column_info import ColumnInfo
from app.repositories.qdrant.base_qdrant_repository import BaseQdrantRepository


class ColumnQdrantRepository(BaseQdrantRepository[ColumnInfo]):
    """负责字段向量集合的创建 写入和基础检索"""

    entity_cls = ColumnInfo

    def __init__(self, client: AsyncQdrantClient):
        super().__init__(client, app_config.qdrant.column_collection)
