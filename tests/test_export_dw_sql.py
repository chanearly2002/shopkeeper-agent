"""
数仓 SQL 快照导出测试

覆盖字面量渲染和分批 INSERT，并检查仓库里的快照与当前生成器、dw.sql 一致：
改了数据生成器或建表语句却忘了重新导出时，这里会失败
"""

import gzip
import hashlib
import io
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.scripts.dw_generator import DEFAULT_SEED, generate
from app.scripts.export_dw_sql import (
    DEFAULT_OUTPUT,
    insert_statements,
    load_table_ddl,
    sql_literal,
    write_snapshot,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "1"),
        (42, "42"),
        (Decimal("12.50"), "12.50"),
        (date(2025, 1, 2), "'2025-01-02'"),
        (datetime(2025, 1, 2, 3, 4, 5), "'2025-01-02 03:04:05'"),
        ("华东", "'华东'"),
        ("O'Neil", "'O\\'Neil'"),
        ("a\\b\nc", "'a\\\\b\\nc'"),
    ],
)
def test_sql_literal(value, expected):
    assert sql_literal(value) == expected


def test_sql_literal_rejects_unknown_type():
    with pytest.raises(TypeError):
        sql_literal(object())


def test_insert_statements_are_batched():
    rows = [{"id": i, "name": f"n{i}"} for i in range(5)]
    statements = list(insert_statements("t", rows, batch=2))
    assert len(statements) == 3
    assert statements[0].startswith("INSERT INTO `t` (`id`, `name`) VALUES\n")
    assert statements[-1] == "INSERT INTO `t` (`id`, `name`) VALUES\n(4, 'n4');\n"
    assert list(insert_statements("t", [])) == []


def test_ddl_covers_all_tables():
    statements = load_table_ddl()
    creates = [s for s in statements if s.upper().startswith("CREATE TABLE")]
    drops = [s for s in statements if s.upper().startswith("DROP TABLE")]
    assert len(creates) == len(drops) == 11


def test_committed_snapshot_is_up_to_date():
    """仓库里的快照必须与当前生成器和 dw.sql 一致，否则重新运行 export_dw_sql"""
    data = generate(scale="medium", seed=DEFAULT_SEED)
    out = io.StringIO()
    write_snapshot(out, data, "dw", f"scale=medium，seed={DEFAULT_SEED}，库名 dw")
    expected = hashlib.sha256(out.getvalue().encode("utf-8")).hexdigest()
    with gzip.open(DEFAULT_OUTPUT, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    assert actual == expected, (
        "docker/snapshot/dw_snapshot.sql.gz 已过期，"
        "请运行 uv run python -m app.scripts.export_dw_sql"
    )
