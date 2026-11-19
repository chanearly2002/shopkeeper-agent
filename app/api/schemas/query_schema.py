"""
问数接口请求体定义

集中声明 API 层输入输出的数据结构，让路由函数只处理业务流程，
字段校验和 OpenAPI 文档生成交给 Pydantic 与 FastAPI 完成。
"""

from datetime import date

from pydantic import BaseModel


class QuerySchema(BaseModel):
    """`/api/query` 请求体，承载用户输入的自然语言问题"""

    # 前端请求体中的 query 字段，例如 {"query": "2025 年华东地区的支付金额"}
    query: str
    # 可选：指定本次查询的“今天”（YYYY-MM-DD），用于复现相对时间问题；不传则用配置
    today: date | None = None
