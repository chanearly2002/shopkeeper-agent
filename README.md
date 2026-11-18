<div align="center">

# 电商问数 · shopkeeper-agent

**面向电商数仓的自然语言问数 Agent**

用中文提问，系统自动召回相关的表、字段、指标和取值，生成并校验 SQL，在数仓上执行后流式返回结果。

![Python](https://img.shields.io/badge/Python-3.14-3776AB.svg?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent%20Workflow-1C3C3C.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688.svg?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB.svg?logo=react&logoColor=black)
![License](https://img.shields.io/badge/License-MIT-green.svg)

</div>

![电商问数前端首页](docs/images/shopkeeper-agent-home.jpg)

## 项目简介

业务人员通常不会写 SQL，也记不住每张表的结构、字段含义和指标口径。直接把问题交给大模型生成 SQL，容易出现选错表、用错字段、指标口径不一致，甚至编造字段等问题。

shopkeeper-agent 的做法是**先检索、再生成、最后校验执行**：

1. **离线构建元数据知识库**：把数仓中的表、字段、指标及字段真实取值，分别写入 MySQL（结构化元数据）、Qdrant（语义向量）和 Elasticsearch（取值全文索引）。
2. **在线问数**：用 LangGraph 编排多步流程——抽取关键词、三路并行召回、合并与过滤上下文、生成 SQL、只读校验与自动修正、执行查询。每一步的进度都通过 SSE 实时推送到前端。

例如问“统计华北地区的销售总额”，系统会召回 `dim_region.region_name` 字段和取值“华北”、`fact_order.order_amount` 字段和 GMV 指标，补齐两表关联所需的主外键，生成并执行对应的 join 查询。

## 核心特性

- **混合检索**：字段和指标用 Qdrant 做语义召回，字段取值用 Elasticsearch + IK 分词做全文召回，三路并行；召回结果再从 MySQL 补齐指标依赖字段和主外键。
- **分步推理**：检索词扩展、表字段过滤、指标过滤、SQL 生成各由独立节点完成。大模型只负责“选择”，上下文结构的裁剪由程序完成。
- **SQL 安全**：执行前用 sqlglot 做程序侧只读校验，只允许单条查询语句，拒绝写入、DDL、加锁和导出；查询有行数上限和执行超时。
- **校验修正闭环**：先用 `EXPLAIN` 校验 SQL，失败时带着数据库报错交给大模型修正，再重新校验，修正次数有上限。
- **指标口径统一**：GMV、AOV 等指标的计算公式写在元数据配置里，生成 SQL 时严格遵循。
- **过程可观测**：前端实时展示流程图中每个节点的状态、重试次数和失败原因，以及最终执行的 SQL；后端日志按 request_id 串联。
- **构建幂等**：元数据知识库可以重复构建，修改配置后重跑即可，不会产生重复数据。

## 系统架构

![电商问数系统架构图](docs/images/shopkeeper-agent-system-architecture.svg)

### 问数流程

```
extract_keywords ─┬─ recall_column ─┐
                  ├─ recall_value  ─┼─ merge_retrieved_info ─┬─ filter_table  ─┐
                  └─ recall_metric ─┘                        └─ filter_metric ─┴─ add_extra_context
  ─ generate_sql ─ validate_sql ─┬─(通过)─ run_sql ─ 结束
                    ▲            └─(有误)─ correct_sql ─┐
                    └───────────────────────────────────┘   修正次数用完则返回错误
```

| 节点 | 作用 |
| --- | --- |
| `extract_keywords` | jieba 抽取关键词，原始问题本身也作为检索词保留 |
| `recall_column` / `recall_metric` | 大模型扩展检索词，向量化后在 Qdrant 中召回字段和指标 |
| `recall_value` | 大模型扩展取值候选，在 Elasticsearch 中召回字段真实取值（如“华北”） |
| `merge_retrieved_info` | 合并三路结果，补齐指标依赖字段和表的主外键，按表组织上下文 |
| `filter_table` / `filter_metric` | 大模型挑出回答问题真正需要的表、字段和指标 |
| `add_extra_context` | 补充当前日期、季度和数据库版本 |
| `generate_sql` / `correct_sql` | 生成 SQL；校验失败时根据报错做最小修正 |
| `validate_sql` | 只读校验 + `EXPLAIN` |
| `run_sql` | 执行查询，返回结果和最终 SQL |

![查询结果页：执行流程与结果表格](docs/images/shopkeeper-agent-query-result.jpg)

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 智能体编排 | LangGraph、LangChain |
| 大模型 | 任意兼容 OpenAI 接口的模型（默认通过硅基流动调用） |
| Embedding | TEI + `BAAI/bge-large-zh-v1.5`（1024 维） |
| 向量检索 | Qdrant |
| 全文检索 | Elasticsearch 8.19 + IK 分词 |
| 元数据库 / 数仓 | MySQL 8、SQLAlchemy（async）、asyncmy |
| SQL 解析与校验 | sqlglot |
| 后端接口 | FastAPI、SSE |
| 前端 | React 19、Vite、Tailwind CSS、TypeScript |
| 配置与日志 | OmegaConf、loguru |
| 工程工具 | uv、pnpm、ruff、pytest、Docker Compose |

## 项目结构

```text
shopkeeper-agent/
├── app/
│   ├── agent/            # LangGraph 图、State、Context 和各节点
│   ├── api/              # FastAPI 路由、依赖注入、生命周期、请求结构
│   ├── clients/          # MySQL、Qdrant、Elasticsearch、Embedding 客户端
│   ├── conf/             # 配置 dataclass 与加载
│   ├── core/             # 日志、request_id 上下文、SQL 安全校验
│   ├── entities/         # 业务实体
│   ├── models/           # SQLAlchemy ORM 模型（元数据库）
│   ├── prompt/           # Prompt 加载工具
│   ├── repositories/     # MySQL、Qdrant、Elasticsearch 数据访问层
│   ├── scripts/          # 元数据知识库构建脚本
│   └── services/         # 知识库构建服务、问数查询服务
├── conf/                 # app_config.yaml（运行配置）、meta_config.yaml（元数据配置）
├── docker/               # Docker Compose、MySQL 初始化 SQL、ES IK 插件
├── docs/                 # 架构图、截图、设计文档
├── frontend/             # React 前端
├── prompts/              # 各节点的 Prompt 模板
├── tests/                # 单元测试
├── main.py               # FastAPI 应用入口
└── pyproject.toml
```

## 快速开始

### 1. 环境要求

- Python ≥ 3.14 与 [uv](https://docs.astral.sh/uv/)
- Node.js 与 pnpm
- Docker 与 Docker Compose

### 2. 安装依赖并配置大模型

```bash
uv sync
cp .env.example .env
```

在 `.env` 中填入大模型 API Key：

```bash
LLM_API_KEY=your_real_api_key
```

默认使用兼容 OpenAI 接口的硅基流动服务。换用其他平台时，修改 `conf/app_config.yaml` 中 `llm` 段的 `model_name` 和 `base_url`。

### 3. 下载 Embedding 模型

模型文件较大，不随仓库提交，需要下载到 Docker 挂载目录：

```bash
uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```

### 4. 启动基础服务

```bash
docker compose -f docker/docker-compose.yaml up -d
```

| 服务 | 端口 |
| --- | --- |
| MySQL | 3306 |
| Elasticsearch | 9200 |
| Kibana | 5601 |
| Qdrant | 6333 |
| Embedding (TEI) | 8081 |

MySQL 容器首次启动时，会自动执行 `docker/mysql/meta.sql` 和 `docker/mysql/dw.sql`，初始化元数据库和示例数仓。

### 5. 构建元数据知识库

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

这一步会把表、字段、指标写入 Meta MySQL，把字段和指标的向量写入 Qdrant，并把字段真实取值写入 Elasticsearch。

构建是幂等的，修改 `conf/meta_config.yaml` 后直接重跑即可。如果在配置中删除了整张表或某个指标，需要加 `--reset` 先清空知识库：

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml --reset
```

### 6. 启动后端和前端

```bash
uv run fastapi dev main.py
```

```bash
cd frontend
pnpm install
pnpm dev
```

打开 <http://localhost:5173> 即可使用。前端开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`；如需修改，复制 `frontend/.env.example` 为 `frontend/.env` 后调整 `VITE_DEV_PROXY_TARGET`。

## 接口说明

`POST /api/query`

```json
{ "query": "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序" }
```

响应为 SSE 流，每条消息形如 `data: {json}`：

| type | 字段 | 含义 |
| --- | --- | --- |
| `progress` | `step`、`status`（running / success / error）、`detail`（可选） | 节点执行进度；`detail` 携带失败原因，例如 SQL 校验报错 |
| `result` | `data`、`sql`、`truncated`、`max_rows` | 查询结果、最终执行的 SQL、是否因超过行数上限被截断 |
| `error` | `message` | 整次查询失败 |

## 示例数据

`docker/mysql/dw.sql` 提供了一个星型模型的小型电商数仓，数据只覆盖 **2025-01-01 ～ 2025-03-31**：

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `fact_order` | 115 | 订单事实表，一行一个订单，包含购买数量和订单金额 |
| `dim_date` | 90 | 日期维度（年、季度、月、日） |
| `dim_customer` | 20 | 客户维度（姓名、性别、会员等级） |
| `dim_product` | 15 | 商品维度（名称、品类、品牌） |
| `dim_region` | 6 | 地区维度（省份、大区、国家） |

`conf/meta_config.yaml` 中定义的指标：

| 指标 | 含义 | 计算口径 |
| --- | --- | --- |
| GMV | 成交总额 | `SUM(order_amount)` |
| AOV | 平均订单金额 | `SUM(order_amount) / COUNT(DISTINCT order_id)` |
| ASP | 件单价 | `SUM(order_amount) / SUM(order_quantity)` |

由于数据只有一个季度，`conf/app_config.yaml` 中的 `agent.today` 默认固定为 `2025-03-31`，这样“本月”“最近 7 天”这类相对时间的问题也能查到数据。接入真实数仓时，把它设为 `null`，就会使用真实的当前日期。

## 主要配置

运行配置位于 `conf/app_config.yaml`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm.model_name` / `llm.base_url` | GLM-5.1 / 硅基流动 | 大模型，需兼容 OpenAI 接口 |
| `qdrant.score_threshold` / `qdrant.limit` | 0.6 / 20 | 字段和指标召回的余弦相似度阈值与条数 |
| `es.min_score` / `es.limit` | 2.0 / 20 | 取值召回的 BM25 得分阈值与条数（未归一化，需按数据标定） |
| `agent.max_correct_attempts` | 2 | SQL 校验失败后的最多修正次数 |
| `agent.max_result_rows` | 1000 | 单次查询最多返回的行数 |
| `agent.max_execution_ms` | 30000 | 单条查询最长执行时间（毫秒） |
| `agent.today` | `2025-03-31` | 注入给模型的“今天”，`null` 表示使用真实日期 |
| `api.cors_origins` | `localhost:5173` | 允许跨域访问的前端地址 |

要接入自己的数仓，需要修改 `conf/meta_config.yaml`：声明要纳入的表和字段（角色、描述、别名、是否同步取值）以及指标（描述、计算公式、依赖字段），然后重新构建知识库。

## 开发

```bash
uv run ruff check --fix .   # 代码检查
uv run ruff format .        # 格式化
uv run pytest               # 单元测试
cd frontend && pnpm lint    # 前端类型检查
```

架构说明、开发约定和已知问题见 [CLAUDE.md](CLAUDE.md)。

## 当前限制与规划

- **单轮问答**：暂不支持多轮追问和会话管理，每个问题独立处理。
- **缺少评测体系**：还没有评测集和自动化回归测试，改动 Prompt 或模型后，无法量化效果变化。
- **生产化能力**：权限控制、多租户、查询缓存、限流、监控告警等暂未涉及。

评测体系和多轮问数的设计方案见 [docs/design/eval-and-multiturn.md](docs/design/eval-and-multiturn.md)。

## 致谢与许可证

本项目基于 [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent) 二次开发。原项目整理自尚硅谷「大模型智能体掌柜问数」项目。

以 [MIT License](LICENSE) 开源。
