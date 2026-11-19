"""
评测用的 LLM 调用缓存

按“模型参数 + 完整提示词”缓存模型回答，保存在本地 SQLite 文件里：
- 只改代码逻辑、提示词没变时，重跑评测直接命中缓存，结果可复现且不花钱
- 改了提示词或模型，对应的调用自然不命中，会真实调用一次并写入缓存
基于标准库 sqlite3 实现 LangChain 的 BaseCache，不引入额外依赖
"""

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.load import dumps, loads


class SQLiteLLMCache(BaseCache):
    """以 SQLite 文件持久化的 LLM 缓存"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "create table if not exists llm_cache (key text primary key, value text not null)"
        )
        self._conn.commit()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(prompt: str, llm_string: str) -> str:
        return hashlib.sha256(f"{llm_string}\n{prompt}".encode()).hexdigest()

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        with self._lock:
            row = self._conn.execute(
                "select value from llm_cache where key = ?",
                (self._key(prompt, llm_string),),
            ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return loads(row[0])

    def update(self, prompt: str, llm_string: str, return_val: RETURN_VAL_TYPE) -> None:
        with self._lock:
            self._conn.execute(
                "insert or replace into llm_cache (key, value) values (?, ?)",
                (self._key(prompt, llm_string), dumps(return_val)),
            )
            self._conn.commit()

    def clear(self, **kwargs: Any) -> None:
        with self._lock:
            self._conn.execute("delete from llm_cache")
            self._conn.commit()

    # 读写都很快，直接在事件循环里同步执行，避免线程池里并发访问同一个连接
    async def alookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        return self.lookup(prompt, llm_string)

    async def aupdate(
        self, prompt: str, llm_string: str, return_val: RETURN_VAL_TYPE
    ) -> None:
        self.update(prompt, llm_string, return_val)
