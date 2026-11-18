"""
元数据配置测试

直接加载仓库里的 conf/meta_config.yaml，保证外键 ref 与指标依赖字段都指向真实存在的字段；
同时验证配置中的表和字段与 docker/mysql/dw.sql 的建表语句一致，避免两边改漏
"""

import re
from pathlib import Path

from omegaconf import OmegaConf

from app.conf.meta_config import (
    ColumnConfig,
    MetaConfig,
    MetricConfig,
    TableConfig,
    validate_meta_config,
)

ROOT = Path(__file__).parents[1]


def load_meta_config() -> MetaConfig:
    context = OmegaConf.load(ROOT / "conf" / "meta_config.yaml")
    return OmegaConf.to_object(
        OmegaConf.merge(OmegaConf.structured(MetaConfig), context)
    )


def ddl_columns() -> dict[str, set[str]]:
    """从 dw.sql 解析出 {表名: 字段名集合}"""
    sql = (ROOT / "docker" / "mysql" / "dw.sql").read_text(encoding="utf-8")
    tables = {}
    for name, body in re.findall(
        r"CREATE TABLE (\w+)\s*\((.*?)\)\s*COMMENT", sql, re.S
    ):
        tables[name] = {
            m.group(1)
            for line in body.splitlines()
            if (m := re.match(r"\s*(\w+)\s+(?!KEY)[A-Z]", line))
            and m.group(1) != "INDEX"
        }
    return tables


def test_repo_meta_config_is_consistent():
    assert validate_meta_config(load_meta_config()) == []


def test_meta_config_matches_dw_ddl():
    ddl = ddl_columns()
    config = load_meta_config()
    assert {t.name for t in config.tables} == set(ddl)
    for table in config.tables:
        assert {c.name for c in table.columns} == ddl[table.name], table.name


def _config(columns: list[ColumnConfig], metrics: list[MetricConfig] | None = None):
    return MetaConfig(
        tables=[
            TableConfig(
                name="dim_a",
                role="dim",
                description="",
                columns=[ColumnConfig("id", "primary_key", "", [], False)],
            ),
            TableConfig(name="fact_b", role="fact", description="", columns=columns),
        ],
        metrics=metrics,
    )


def test_validate_reports_missing_and_dangling_ref():
    errors = validate_meta_config(
        _config(
            [
                ColumnConfig("a_id", "foreign_key", "", [], False),
                ColumnConfig("x_id", "foreign_key", "", [], False, ref="dim_a.nope"),
                ColumnConfig("amount", "measure", "", [], False, ref="dim_a.id"),
            ],
            [MetricConfig("m", "", ["fact_b.missing"], [])],
        )
    )
    assert any("fact_b.a_id 缺少 ref" in e for e in errors)
    assert any("dim_a.nope" in e for e in errors)
    assert any("不是外键" in e for e in errors)
    assert any("fact_b.missing" in e for e in errors)
