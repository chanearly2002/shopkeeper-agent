"""
字段取值 ES 仓储

把字段真实取值组织成 Elasticsearch 全文索引，并提供索引创建 批量写入和关键词检索能力

Service 层负责决定哪些字段需要同步
Repository 只关心索引是否存在 ValueInfo 如何写进 ES 以及如何按关键词召回
索引名和检索阈值来自 conf/app_config.yaml 的 es 段
"""

from dataclasses import asdict

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import app_config
from app.entities.value_info import ValueInfo


class ValueESRepository:
    """负责字段取值全文索引的创建 写入和基础检索"""

    # value 字段使用 IK 分词，这样地区 会员等级 品类等中文值才能按全文方式检索
    index_mappings = {
        "dynamic": False,
        "properties": {
            "id": {"type": "keyword"},
            "value": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_max_word",
            },
            "column_id": {"type": "keyword"},
        },
    }

    def __init__(self, client: AsyncElasticsearch, index_name: str | None = None):
        self.client = client
        self.index_name = index_name or app_config.es.index_name

    async def ensure_index(self):
        """确保字段取值索引已经创建好"""
        if not await self.client.indices.exists(index=self.index_name):
            await self.client.indices.create(
                index=self.index_name, mappings=self.index_mappings
            )

    async def delete_index(self):
        """删除整个索引，用于全量重建"""
        if await self.client.indices.exists(index=self.index_name):
            await self.client.indices.delete(index=self.index_name)

    async def delete_by_column_ids(self, column_ids: list[str]):
        """删除指定字段的全部取值，重新同步这些字段前调用，清掉数仓里已不存在的旧值"""
        if not column_ids:
            return
        await self.client.delete_by_query(
            index=self.index_name,
            query={"terms": {"column_id": column_ids}},
            refresh=True,
            conflicts="proceed",
        )

    async def index(self, value_infos: list[ValueInfo], batch_size=500):
        """分批写入字段取值，避免一次 bulk 过大"""
        if not value_infos:
            return

        for i in range(0, len(value_infos), batch_size):
            batch_value_infos = value_infos[i : i + batch_size]
            batch_operations = []
            for value_info in batch_value_infos:
                # 用 ValueInfo.id 作为文档 id，这样重复构建时会覆盖同一条值记录
                batch_operations.append(
                    {"index": {"_index": self.index_name, "_id": value_info.id}}
                )
                batch_operations.append(asdict(value_info))
            resp = await self.client.bulk(operations=batch_operations)
            # bulk 接口部分失败时不会抛异常，需要主动检查 errors 标记
            if resp.get("errors"):
                failed = [
                    item["index"]["error"]
                    for item in resp["items"]
                    if "error" in item["index"]
                ]
                raise RuntimeError(f"写入字段取值失败 {len(failed)} 条：{failed[:3]}")

        # 构建完成后立即刷新，保证随后的检索能看到新写入的文档
        await self.client.indices.refresh(index=self.index_name)

    async def search(
        self,
        keyword: str,
        min_score: float | None = None,
        limit: int | None = None,
    ) -> list[ValueInfo]:
        """按关键词全文检索字段取值，并还原为 ValueInfo 实体"""
        resp = await self.client.search(
            index=self.index_name,
            # value 字段启用了 IK 分词，match 查询可以处理中文短语和枚举值匹配
            query={"match": {"value": keyword}},
            size=limit or app_config.es.limit,
            # 过滤掉相关度过低的命中；注意这是 BM25 原始得分，不是 0~1 的相似度
            min_score=app_config.es.min_score if min_score is None else min_score,
        )
        # ES 文档 _source 中保存的是 ValueInfo 的字段结构，业务层继续使用实体对象
        return [ValueInfo(**hit["_source"]) for hit in resp["hits"]["hits"]]
