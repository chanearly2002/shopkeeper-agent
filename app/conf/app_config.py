"""
应用主配置

定义 conf/app_config.yaml 在程序中的结构化配置对象
项目启动后会在这里一次性完成配置文件加载和类型化转换，其他模块只需要导入 app_config
就可以按属性方式读取日志 MySQL Qdrant Embedding Elasticsearch 和 LLM 配置
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from omegaconf import OmegaConf


@dataclass
class File:
    """文件日志配置"""

    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    """控制台日志配置"""

    enable: bool
    level: str


@dataclass
class LoggingConfig:
    """日志总配置"""

    file: File
    console: Console


@dataclass
class DBConfig:
    """MySQL 连接配置"""

    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    """Qdrant 连接与向量维度配置"""

    host: str
    port: int
    embedding_size: int
    column_collection: str
    metric_collection: str
    score_threshold: float
    limit: int


@dataclass
class EmbeddingConfig:
    """Embedding 服务配置"""

    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    """Elasticsearch 配置"""

    host: str
    port: int
    index_name: str
    min_score: float
    limit: int


@dataclass
class LLMConfig:
    """大模型调用配置"""

    model_name: str
    api_key: str
    base_url: str


@dataclass
class KnowledgeConfig:
    """业务术语与已验证问答示例配置"""

    term_collection: str
    example_collection: str
    enable_examples: bool
    example_top_k: int
    example_score_threshold: float


@dataclass
class AgentConfig:
    """问数智能体运行参数"""

    max_correct_attempts: int
    semantic_check: bool
    max_result_rows: int
    max_execution_ms: int
    # 形如 2025-12-31；为 None 时使用真实当前日期
    today: Optional[str] = None


@dataclass
class ConversationConfig:
    """多轮问数配置"""

    history_turns: int
    result_preview_rows: int
    answer_max_rows: int
    enable_clarify: bool
    contextualize_first_turn: bool


@dataclass
class ApiConfig:
    """HTTP 接口配置"""

    cors_origins: list[str]


@dataclass
class AppConfig:
    """项目级总配置入口"""

    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    knowledge: KnowledgeConfig
    agent: AgentConfig
    conversation: ConversationConfig
    api: ApiConfig


# 从当前文件位置回到项目根目录，再定位到 conf/app_config.yaml
project_root = Path(__file__).parents[2]
config_file = project_root / "conf" / "app_config.yaml"

# 先读取本地 .env，让 YAML 中的 ${oc.env:...} 可以解析到敏感配置
load_dotenv(project_root / ".env")

# 读取 YAML 配置内容
context = OmegaConf.load(config_file)

# 根据 AppConfig 生成结构化配置 schema
schema = OmegaConf.structured(AppConfig)

# 把配置结构和配置值合并，再转换成可以直接按属性访问的对象
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))

if __name__ == "__main__":
    # 简单测试：验证配置是否能正常读取
    print(app_config.es.host)
