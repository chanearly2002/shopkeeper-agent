"""
教学数仓数据写入脚本入口

先按 docker/mysql/dw.sql 中的 DROP / CREATE TABLE 语句重建 dw 库的全部表，
再把 dw_generator 生成的数据分批写入。每次运行都会得到完全相同的数据，
评测集里的标准答案依赖这一点，所以不要在这里引入任何不固定的随机性

用法：
    uv run python -m app.scripts.generate_dw_data              # 默认 medium 规模
    uv run python -m app.scripts.generate_dw_data --scale small
"""

import argparse
import asyncio
import re
import time
from pathlib import Path

from sqlalchemy import text

from app.clients.mysql_client_manager import dw_mysql_client_manager
from app.core.log import logger
from app.scripts.dw_generator import DEFAULT_SEED, SCALES, generate

DW_SQL = Path(__file__).parents[2] / "docker" / "mysql" / "dw.sql"


def load_table_ddl() -> list[str]:
    """从 dw.sql 中取出 DROP TABLE 和 CREATE TABLE 语句，跳过建库 授权等需要高权限的语句"""
    # 去掉整行注释后按分号切分
    content = "\n".join(
        line
        for line in DW_SQL.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in content.split(";") if s.strip()]
    return [s for s in statements if re.match(r"(DROP|CREATE)\s+TABLE", s, re.I)]


async def write(scale: str, seed: int, batch_size: int = 2000):
    """重建表并写入生成的数据"""
    started = time.perf_counter()
    data = generate(scale=scale, seed=seed)
    logger.info(f"数据生成完成，用时 {time.perf_counter() - started:.1f} 秒")

    dw_mysql_client_manager.init()
    try:
        async with dw_mysql_client_manager.session_factory() as session:
            async with session.begin():
                for statement in load_table_ddl():
                    await session.execute(text(statement))
            logger.info("已按 dw.sql 重建全部表")

            for table, rows in data.items():
                if not rows:
                    continue
                columns = list(rows[0].keys())
                sql = text(
                    f"insert into {table} ({', '.join(columns)}) "
                    f"values ({', '.join(':' + c for c in columns)})"
                )
                # 每张表单独提交，失败时能直接看出是哪张表
                async with session.begin():
                    for i in range(0, len(rows), batch_size):
                        await session.execute(sql, rows[i : i + batch_size])
                logger.info(f"{table}: 写入 {len(rows)} 行")
    finally:
        await dw_mysql_client_manager.close()

    logger.info(f"全部完成，总用时 {time.perf_counter() - started:.1f} 秒")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成并写入教学数仓数据")
    parser.add_argument("--scale", choices=sorted(SCALES), default="medium")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    asyncio.run(write(args.scale, args.seed))
