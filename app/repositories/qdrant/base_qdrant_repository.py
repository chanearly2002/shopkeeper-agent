"""
Qdrant 向量仓储基类

字段和指标两类向量集合的读写逻辑完全一致，只有集合名和 payload 还原成的实体不同
这里统一实现集合创建、按实体替换写入和相似度检索，子类只声明集合名和实体类型

一个实体（字段或指标）会按名称、描述、每个别名拆成多个 point，payload 中的 id 是实体 id
重复构建时先按实体 id 删除旧 point 再写入，point id 也由实体 id 和文本确定性生成，
这样重跑构建不会产生重复向量，别名删改后旧的 point 也会被清掉
"""

import uuid
from typing import Any, ClassVar

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    PointStruct,
    VectorParams,
)

from app.conf.app_config import app_config

# 固定命名空间，保证同一实体同一文本在任何一次构建中都得到相同的 point id
_POINT_ID_NAMESPACE = uuid.UUID("6c5f8a43-2a8e-4f0b-9d55-3f7b8c2f1e01")


def make_point_id(entity_id: str, text: str) -> str:
    """由实体 id 和向量化文本生成确定性的 point id"""
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, f"{entity_id}|{text}"))


class BaseQdrantRepository[EntityT]:
    """负责单个向量集合的创建 替换写入和基础检索"""

    # 子类必须声明 payload 还原成的实体类型
    entity_cls: ClassVar[type]

    def __init__(self, client: AsyncQdrantClient, collection_name: str):
        self.client = client
        self.collection_name = collection_name

    async def ensure_collection(self):
        """确保集合存在，并按配置中的 Embedding 维度初始化"""
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    # 向量维度必须和 Embedding 模型输出一致，否则写入时会失败
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE,
                ),
            )

    async def delete_collection(self):
        """删除整个集合，用于全量重建"""
        if await self.client.collection_exists(self.collection_name):
            await self.client.delete_collection(self.collection_name)

    async def delete_by_entity_ids(self, entity_ids: list[str]):
        """删除指定实体的全部 point，重建这些实体前调用"""
        if not entity_ids:
            return
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="id", match=MatchAny(any=entity_ids))]
                )
            ),
            wait=True,
        )

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[dict[str, Any]],
        batch_size: int = 10,
    ):
        """分批 upsert 向量点，避免一次提交过多 point"""
        # ids embeddings payloads 三个列表按相同下标组成一条完整的 Qdrant point
        points = [
            PointStruct(id=id, vector=embedding, payload=payload)
            for id, embedding, payload in zip(ids, embeddings, payloads, strict=True)
        ]
        for i in range(0, len(points), batch_size):
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points[i : i + batch_size],
                wait=True,
            )

    async def search(
        self,
        embedding: list[float],
        score_threshold: float | None = None,
        limit: int | None = None,
    ) -> list[EntityT]:
        """按向量相似度检索，并把 payload 还原为业务实体"""
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            limit=limit or app_config.qdrant.limit,
            score_threshold=(
                app_config.qdrant.score_threshold
                if score_threshold is None
                else score_threshold
            ),
        )
        # Qdrant 只保存实体 payload，业务层继续使用实体对象
        return [self.entity_cls(**point.payload) for point in result.points]
