"""
元数据知识构建配置

定义 meta_config.yaml 在程序中的结构，这份配置不描述底层基础设施
而是描述一次元数据知识构建要处理哪些表 哪些字段 和哪些指标，脚本入口会先读取 YAML
再把内容转换成这里定义的 dataclass 对象，后续服务层就可以按统一结构完成元数据同步
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnConfig:
    """
    单个字段的同步配置
    描述字段的业务角色 说明 别名以及是否同步字段值
    """

    name: str
    role: str
    description: str
    alias: list[str]
    sync: bool
    # 外键关联的目标字段，格式 表名.字段名；只有 role 为 foreign_key 的字段需要填写
    ref: Optional[str] = None


@dataclass
class TableConfig:
    """
    单张表及其字段列表的同步配置
    一张表下面会继续声明需要纳入知识库的字段列表
    """

    name: str
    role: str
    description: str
    columns: list[ColumnConfig]


@dataclass
class MetricConfig:
    """
    单个指标的同步配置
    用来描述指标和底层字段之间的关联关系
    """

    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]


@dataclass
class TermConfig:
    """
    业务术语配置
    definition 用自然语言写清口径，涉及的字段用 表名.字段名 写出，构建时会校验这些字段存在，
    问数时命中术语会把这些字段补进候选上下文
    """

    name: str
    definition: str
    alias: list[str] = field(default_factory=list)


@dataclass
class MetaConfig:
    """
    元数据知识构建总配置
    tables 和 metrics 都允许为空 便于分阶段只构建其中一部分
    """

    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
    terms: Optional[list[TermConfig]] = None


# 术语定义里引用字段的写法：dim_xxx.col 或 fact_xxx.col
COLUMN_REF_PATTERN = re.compile(r"\b((?:dim|fact)_\w+\.\w+)\b")


def columns_in_text(text: str) -> list[str]:
    """提取一段文字里以 表名.字段名 形式引用的字段，保持出现顺序并去重"""
    return list(dict.fromkeys(COLUMN_REF_PATTERN.findall(text)))


def validate_meta_config(meta_config: MetaConfig) -> list[str]:
    """
    检查元数据配置的内部一致性，返回错误列表，为空表示通过

    - 外键必须填写 ref，且 ref 指向配置中存在的字段；非外键不应填写 ref
    - 指标依赖的字段必须在配置中存在
    这些错误不会让构建报错，却会让模型拿到错误的关联关系，所以在构建前统一拦截
    """
    errors: list[str] = []
    column_ids = {
        f"{table.name}.{column.name}"
        for table in meta_config.tables or []
        for column in table.columns
    }
    for table in meta_config.tables or []:
        for column in table.columns:
            column_id = f"{table.name}.{column.name}"
            if column.role == "foreign_key" and not column.ref:
                errors.append(f"外键 {column_id} 缺少 ref")
            if column.ref and column.role != "foreign_key":
                errors.append(f"{column_id} 不是外键，却填写了 ref")
            if column.ref and column.ref not in column_ids:
                errors.append(f"{column_id} 的 ref 指向不存在的字段 {column.ref}")
    if meta_config.tables:
        for metric in meta_config.metrics or []:
            for relevant_column in metric.relevant_columns:
                if relevant_column not in column_ids:
                    errors.append(
                        f"指标 {metric.name} 依赖的字段 {relevant_column} 不存在"
                    )
        for term in meta_config.terms or []:
            for column_id in columns_in_text(term.definition):
                if column_id not in column_ids:
                    errors.append(f"术语 {term.name} 引用的字段 {column_id} 不存在")
    return errors
