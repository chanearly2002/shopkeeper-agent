"""
业务术语实体

把业务人员的说法映射到确定的口径，例如“高价值会员”= 铂金和钻石会员、“双11当天”= 11 月 11 日
这类知识由人维护，比让模型猜更可靠（参考 SQLBot 的术语配置）
"""

from dataclasses import dataclass, field


@dataclass
class TermInfo:
    """一条业务术语"""

    id: str
    name: str
    definition: str
    alias: list[str] = field(default_factory=list)
