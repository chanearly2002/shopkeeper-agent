"""
导出教学数仓的固定 SQL 快照

用固定种子的数据生成器 + docker/mysql/dw.sql 的建表语句，直接渲染出一个自包含的 SQL 文件
（建库、建表、全部数据），不需要连接数据库。导入后的数据与 generate_dw_data 写入的完全一致，
适合在本地 MySQL 客户端里探查数据结构，或在没有 Python 环境的机器上复现数仓

用法：
    uv run python -m app.scripts.export_dw_sql                          # 写到 docker/snapshot/dw_snapshot.sql.gz
    uv run python -m app.scripts.export_dw_sql -o dw.sql                # 不压缩
    uv run python -m app.scripts.export_dw_sql --database dw_explore    # 导入到另一个库名，不覆盖 dw

本模块不导入项目配置（不需要 LLM_API_KEY），渲染函数有单元测试
"""

import argparse
import gzip
import io
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, TextIO

from app.scripts.dw_generator import DEFAULT_SEED, SCALES, generate

ROOT = Path(__file__).parents[2]
DW_SQL = ROOT / "docker" / "mysql" / "dw.sql"
DEFAULT_OUTPUT = ROOT / "docker" / "snapshot" / "dw_snapshot.sql.gz"
# 每条 INSERT 的行数：太大会超过 max_allowed_packet，太小导入慢
ROWS_PER_INSERT = 1000

# MySQL 默认 sql_mode 下字符串字面量需要转义的字符
_ESCAPES = {
    "\\": "\\\\",
    "'": "\\'",
    "\n": "\\n",
    "\r": "\\r",
    "\0": "\\0",
    "\x1a": "\\Z",
}
_ESCAPE_RE = re.compile("[" + re.escape("".join(_ESCAPES)) + "]")


def load_table_ddl(path: Path = DW_SQL) -> list[str]:
    """从 dw.sql 中取出 DROP TABLE 和 CREATE TABLE 语句，跳过建库 授权等需要高权限的语句"""
    # 去掉整行注释后按分号切分
    content = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in content.split(";") if s.strip()]
    return [s for s in statements if re.match(r"(DROP|CREATE)\s+TABLE", s, re.I)]


def sql_literal(value: Any) -> str:
    """把生成器产出的 Python 值渲染成 MySQL 字面量"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # datetime 是 date 的子类，必须先判断
    if isinstance(value, datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group()], value) + "'"
    raise TypeError(f"不支持导出的值类型：{type(value).__name__}")


def insert_statements(
    table: str, rows: list[dict], batch: int = ROWS_PER_INSERT
) -> Iterable[str]:
    """按批渲染多行 INSERT 语句，列顺序取第一行的键顺序"""
    if not rows:
        return
    columns = list(rows[0].keys())
    head = f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) VALUES\n"
    for i in range(0, len(rows), batch):
        values = ",\n".join(
            "(" + ", ".join(sql_literal(row[c]) for c in columns) + ")"
            for row in rows[i : i + batch]
        )
        yield head + values + ";\n"


def write_snapshot(
    out: TextIO, data: dict[str, list[dict]], database: str, meta: str
) -> None:
    """写出完整快照：文件头说明、建库、建表、数据"""
    out.write(
        f"-- 电商问数教学数仓快照（{meta}）\n"
        "-- 由 app/scripts/export_dw_sql.py 生成，请勿手工修改；数据生成器或 dw.sql 变化后重新导出\n"
        "-- 表结构与 docker/mysql/dw.sql 完全一致，数据与 generate_dw_data 写入的完全一致\n"
        "-- 行数：" + "，".join(f"{t} {len(rows)}" for t, rows in data.items()) + "\n\n"
        "SET NAMES utf8mb4;\n"
        "SET autocommit = 0;\n"
        "SET unique_checks = 0;\n\n"
        f"CREATE DATABASE IF NOT EXISTS `{database}` "
        "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n"
        f"USE `{database}`;\n\n"
    )
    for statement in load_table_ddl():
        out.write(statement + ";\n\n")
    for table, rows in data.items():
        out.write(f"-- {table}：{len(rows)} 行\n")
        for statement in insert_statements(table, rows):
            out.write(statement)
        out.write("COMMIT;\n\n")
    out.write("SET unique_checks = 1;\nSET autocommit = 1;\n")


def export(output: Path, scale: str, seed: int, database: str) -> None:
    """生成数据并写出快照；.gz 结尾时压缩（固定 mtime，相同输入得到相同字节）"""
    started = time.perf_counter()
    data = generate(scale=scale, seed=seed)
    meta = f"scale={scale}，seed={seed}，库名 {database}"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == ".gz":
        with open(output, "wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9
            ) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as out:
                    write_snapshot(out, data, database, meta)
    else:
        with open(output, "w", encoding="utf-8", newline="\n") as out:
            write_snapshot(out, data, database, meta)
    size = output.stat().st_size / 1024 / 1024
    print(
        f"已导出 {output}（{size:.1f} MB，{sum(len(r) for r in data.values())} 行，"
        f"用时 {time.perf_counter() - started:.1f} 秒）"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出教学数仓的固定 SQL 快照")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scale", choices=sorted(SCALES), default="medium")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--database", default="dw", help="导入的目标库名，默认 dw")
    args = parser.parse_args()
    export(args.output, args.scale, args.seed, args.database)
