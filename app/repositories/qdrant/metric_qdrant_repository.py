"""
指标向量仓储

管理指标向量集合 metric_info_collection（集合名来自配置）
字段和指标虽然都用向量检索，但它们是两类不同对象，
所以指标单独使用一个集合，避免召回时和字段结果混在一起
"""

from qdrant_client import AsyncQdrantClient

from app.conf.app_config import app_config
from app.entities.metric_info import MetricInfo
from app.repositories.qdrant.base_qdrant_repository import BaseQdrantRepository


class MetricQdrantRepository(BaseQdrantRepository[MetricInfo]):
    """负责指标向量集合的创建 写入和基础检索"""

    entity_cls = MetricInfo

    def __init__(self, client: AsyncQdrantClient):
        super().__init__(client, app_config.qdrant.metric_collection)
