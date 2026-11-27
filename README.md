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

## 项目简介

业务人员通常不会写 SQL，也记不住每张表的结构、字段含义和指标口径。直接把问题交给大模型生成 SQL，容易出现选错表、用错字段、指标口径不一致，甚至编造字段等问题。

shopkeeper-agent 的做法是**先检索、再生成、最后校验执行**：

1. **离线构建元数据知识库**：把数仓中的表、字段、指标及字段真实取值，分别写入 MySQL（结构化元数据）、Qdrant（语义向量）和 Elasticsearch（取值全文索引）。
2. **在线问数**：用 LangGraph 编排多步流程——抽取关键词、四路并行召回（字段、取值、指标、业务知识）、合并与过滤上下文、生成 SQL、只读与语义校验、自动修正、执行查询。每一步的进度都通过 SSE 实时推送到前端。

例如问“2025 年美妆个护的支付金额”，系统会召回取值“美妆个护”所在的 `dim_category.category_l1` 字段，以及“支付金额”指标的口径（只统计 `pay_time` 不为空的订单）；发现订单表和类目表之间还隔着订单明细表和商品表，就自动补上这两张中间表，再按外键关系生成多表 join 查询。

## 核心特性

- **混合检索**：字段和指标用 Qdrant 做语义召回，字段取值用 Elasticsearch + IK 分词做全文召回，再加上业务术语与已验证示例，四路并行；召回结果再从 MySQL 补齐指标依赖字段和主外键。
- **关联路径补全**：元数据中为每个外键声明关联目标（`ref`），系统据此找出候选表之间缺失的中间表，并让模型按 `ref` 编写 JOIN 条件，避免经由维度表关联造成的金额重复计算。
- **分步推理**：检索词扩展、表字段过滤、指标过滤、SQL 生成各由独立节点完成。大模型只负责“选择”，上下文结构的裁剪由程序完成。
- **SQL 语义检查**：EXPLAIN 之后再按外键关系检查“能执行但算错”的写法（如订单金额关联明细后重复计算），发现问题交给模型修正。
- **业务术语与已验证示例**：术语表把“双11当天 / 双11期间 / 双11活动商品”这类说法映射到确定口径；已验证的问题-SQL 对按相似度召回作为参考写法。
- **多轮追问**：同一会话里的“那华南呢”“按月拆开”“第一名的再看看”会先结合上文改写成独立完整的问题，再走单轮流程，追问时参考上一轮的 SQL 增量修改；“哪个最高”“差多少”这类针对上一轮结果的提问直接基于结果回答、不再查库；“那个品牌呢”这类说不清的问题会先反问。前端展示“我理解为：……”，理解有误可以直接改。
- **评测体系**：50 道业务口径陷阱题 + 22 段多轮追问对话（含结果追问和需要反问的对话），答题打分 runner 按意图、召回、过滤、生成环节归因，支持基线对比和消融实验。
- **SQL 安全**：执行前用 sqlglot 做程序侧只读校验，只允许单条查询语句，拒绝写入、DDL、加锁和导出；查询有行数上限和执行超时。
- **校验修正闭环**：先用 `EXPLAIN` 校验 SQL，失败时带着数据库报错交给大模型修正，再重新校验，修正次数有上限。
- **指标口径统一**：下单金额、支付金额、客单价、退款率等 13 个指标的计算公式写在元数据配置里，生成 SQL 时严格遵循。
- **过程可观测**：前端实时展示流程图中每个节点的状态、重试次数和失败原因，以及最终执行的 SQL；后端日志按 request_id 串联。
- **构建幂等**：元数据知识库可以重复构建，修改配置后重跑即可，不会产生重复数据。

## 系统架构

> 架构图和界面截图沿用自上游项目，已与当前实现不符，暂时移除；本项目开发完成后会按当前实现重新绘制。

### 问数流程

外层是多轮会话图，按会话 id 保存历史；每一轮先把追问改写成独立问题，再按意图分流：

```
contextualize（结合上文改写追问，首轮跳过）─┬─ 需要反问 ─ ask_clarification（暂停等用户回答，回答后重新改写）
                                            ├─ 针对上一轮结果 ─ answer_from_result（直接回答；答不了再查库）
                                            └─ 其他 ─ run_pipeline（单轮流水线，见下；追问时参考上一轮 SQL）
  ─ remember（记录本轮问题、SQL、结果预览或回答）
```

单轮问数流水线：

```
extract_keywords ─┬─ recall_column    ─┐
                  ├─ recall_value     ─┤
                  ├─ recall_metric    ─┼─ merge_retrieved_info ─┬─ filter_table  ─┐
                  └─ recall_knowledge ─┘                        └─ filter_metric ─┴─ add_extra_context
  ─ generate_sql ─ validate_sql ─┬─(通过)─ run_sql ─ 结束
                    ▲            └─(有误)─ correct_sql ─┐
                    └───────────────────────────────────┘   修正次数用完：语法错误则返回错误，仅剩语义提示则告警后执行
```

| 节点 | 作用 |
| --- | --- |
| `extract_keywords` | jieba 抽取关键词，原始问题本身也作为检索词保留 |
| `recall_column` / `recall_metric` | 大模型扩展检索词，向量化后在 Qdrant 中召回字段和指标 |
| `recall_value` | 大模型扩展取值候选，在 Elasticsearch 中召回字段真实取值（如“华北”） |
| `recall_knowledge` | 精确匹配业务术语（如“高价值会员”“双11当天”），按问题相似度召回已验证的问题-SQL 示例 |
| `merge_retrieved_info` | 合并四路结果，补齐指标依赖字段、术语引用的字段、示例用到的表、关联路径上的中间表和各表主外键，按表组织上下文 |
| `filter_table` / `filter_metric` | 大模型挑出回答问题真正需要的表、字段和指标 |
| `add_extra_context` | 补充当前日期、季度和数据库版本 |
| `generate_sql` / `correct_sql` | 参考术语和示例生成 SQL；校验失败时根据报错做最小修正 |
| `validate_sql` | 只读校验 + `EXPLAIN` + 按外键关系的语义检查（扇出、关联条件） |
| `run_sql` | 执行查询，返回结果和最终 SQL |

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 智能体编排 | LangGraph、LangChain |
| 大模型 | 任意兼容 OpenAI 接口的模型（默认用智谱开放平台的 glm-5.3-flash） |
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
│   ├── scripts/          # 示例数据生成、元数据知识库构建脚本
│   └── services/         # 知识库构建服务、问数查询服务
├── conf/                 # app_config.yaml（运行配置）、meta_config.yaml（元数据与术语）、sql_examples.yaml（已验证示例）
├── docker/               # Docker Compose、MySQL 初始化 SQL、ES IK 插件、数仓 SQL 快照
├── docs/                 # 设计文档
├── evals/                # 评测集、判分、评测 runner
├── notebooks/            # 数仓探查 notebook
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

默认使用智谱开放平台的 `glm-5.3-flash`（OpenAI 兼容接口），`LLM_API_KEY` 填智谱的 key。换用其他平台时，修改 `conf/app_config.yaml` 中 `llm` 段的 `model_name` 和 `base_url`。

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

MySQL 容器首次启动时，会自动执行 `docker/mysql/meta.sql` 和 `docker/mysql/dw.sql`，创建元数据库和数仓的表结构。

### 5. 生成示例数据

```bash
uv run python -m app.scripts.generate_dw_data
```

用固定随机种子生成两年的电商交易数据并写入数仓（约 6 万订单、20 万条行为，耗时十几秒）。每次运行都会重建数仓的表，得到完全相同的数据。想快速试用可以加 `--scale small`，订单和行为数据量为默认的十分之一。

也可以不跑 Python，直接导入仓库里的固定 SQL 快照 `docker/snapshot/dw_snapshot.sql.gz`（数据完全相同，适合用 MySQL 客户端探查数据结构，导入方法和探查用的 SQL 见 [docker/snapshot/README.md](docker/snapshot/README.md)）：

```bash
gunzip -c docker/snapshot/dw_snapshot.sql.gz | docker exec -i mysql mysql -uroot -pdili123
```

### 6. 构建元数据知识库

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

这一步会把表、字段、指标写入 Meta MySQL，把字段和指标的向量写入 Qdrant，并把字段真实取值写入 Elasticsearch。

构建是幂等的，修改 `conf/meta_config.yaml` 后直接重跑即可。如果在配置中删除了整张表或某个指标，需要加 `--reset` 先清空知识库：

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml --reset
```

### 7. 启动后端和前端

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
{ "query": "2025 年各大区的支付金额，按从高到低排序" }
```

可选字段：`thread_id`（会话 id，追问时带上，同一会话里的问题会结合上文理解；不传则新建会话），`today`（本次查询的“今天”，`YYYY-MM-DD`），`resume`（回答上一条反问的内容，必须同时带 `thread_id`）。

响应为 SSE 流，每条消息形如 `data: {json}`：

| type | 字段 | 含义 |
| --- | --- | --- |
| `session` | `thread_id` | 每次请求的第一条消息，本轮所属的会话 id |
| `progress` | `step`、`status`（running / success / error）、`detail`（可选） | 节点执行进度；`detail` 携带失败原因，例如 SQL 校验报错 |
| `rewrite` | `standalone_question`、`intent`（new / followup / result_qa） | 结合上文改写后实际查询的问题 |
| `clarify` | `question`、`options` | 信息不足时的反问；回答时把选项或自己写的内容作为 `resume` 发回 |
| `answer` | `text` | 针对上一轮结果的提问，直接基于结果给出的回答（不查库） |
| `result` | `data`、`sql`、`truncated`、`max_rows` | 查询结果、最终执行的 SQL、是否因超过行数上限被截断 |
| `error` | `message` | 整次查询失败 |

## 示例数据

示例数仓参考淘宝用户行为数据集、京东 JData、Olist 等公开电商数据集的表结构设计，是一个 4 张事实表 + 7 张维度表的星型模型（类目为雪花结构），覆盖 **2024-01-01 ～ 2025-12-31** 两年。设计说明见 [docs/design/dw-schema-v2.md](docs/design/dw-schema-v2.md)。

| 表 | 粒度 | 行数 |
| --- | --- | --- |
| `fact_order` | 一笔订单：状态、下单与支付时间、优惠、运费、应付金额 | 约 5.9 万 |
| `fact_order_item` | 订单中的一个 SKU：件数、成交单价、分摊优惠、实付金额、参与的活动 | 约 8.6 万 |
| `fact_refund` | 一次退款申请：类型、原因、状态、退款金额 | 约 5.9 千 |
| `fact_user_behavior` | 一次浏览 / 收藏 / 加购行为 | 约 19 万 |
| `dim_date` | 一天，含季度、周、节假日 | 731 |
| `dim_region` | 一个城市，含城市等级和大区 | 44 |
| `dim_user` | 一个用户，含年龄段、会员等级、是否付费会员 | 3,000 |
| `dim_category` | 一个三级类目 | 40 |
| `dim_shop` | 一个店铺：自营 / 旗舰店 / 专卖店 / 专营店 | 49 |
| `dim_sku` | 一个 SKU，含标价和成本价 | 317 |
| `dim_activity` | 一个营销活动：618、双 11 等大促和日常活动 | 14 |

数据保留了真实业务中的口径差异，用来检验 Agent 生成的 SQL 在业务上是否正确：

- 订单有已取消、待支付状态，**下单金额 ≠ 支付金额**；
- 订单头和订单明细各有一份金额，两表关联后对订单金额求和会被放大约 60%；
- 退款单独成表，只有“退款成功”才应扣减；
- 同一张日期表被下单日期、支付日期等多个外键引用，同一张地区表被收货城市和用户常住城市引用；
- 大促期间销量明显上升，各品类的价格带、毛利率和退款率不同。

`conf/meta_config.yaml` 中定义了 13 个指标，例如：

| 指标 | 计算口径 |
| --- | --- |
| 下单金额（GMV） | `SUM(fact_order.order_amount)`，包含未支付和已取消订单 |
| 支付金额（销售额） | `SUM(fact_order.order_amount)`，限 `pay_time IS NOT NULL` |
| 净支付金额 | 支付金额 − 退款成功的 `refund_amount` |
| 客单价 / 笔单价 | 支付金额 ÷ 支付买家数 / 支付订单数 |
| 件单价 | 已支付明细 `SUM(pay_amount) ÷ SUM(quantity)` |
| 毛利 | 已支付明细 `SUM(pay_amount − cost_price × quantity)` |
| 退款率 | 退款成功金额 ÷ 支付金额 |
| 浏览转化率 | 支付买家数 ÷ 浏览用户数 |
| 复购率 | 支付订单数 ≥ 2 的买家数 ÷ 支付买家数 |

`conf/app_config.yaml` 中的 `agent.today` 默认固定为数据的最后一天 `2025-12-31`，这样“本月”“去年同期”这类相对时间的问题也能查到数据。接入真实数仓时，把它设为 `null`，就会使用真实的当前日期。

## 主要配置

运行配置位于 `conf/app_config.yaml`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm.model_name` / `llm.base_url` | `glm-5.3-flash` / 智谱开放平台 | 大模型，需兼容 OpenAI 接口 |
| `qdrant.score_threshold` / `qdrant.limit` | 0.6 / 20 | 字段和指标召回的余弦相似度阈值与条数 |
| `es.min_score` / `es.limit` | 2.0 / 20 | 取值召回的 BM25 得分阈值与条数（未归一化，需按数据标定） |
| `agent.max_correct_attempts` | 2 | SQL 校验失败后的最多修正次数 |
| `agent.max_result_rows` | 1000 | 单次查询最多返回的行数 |
| `agent.max_execution_ms` | 30000 | 单条查询最长执行时间（毫秒） |
| `agent.today` | `2025-12-31` | 注入给模型的“今天”，`null` 表示使用真实日期 |
| `conversation.history_turns` / `conversation.result_preview_rows` | 3 / 5 | 追问改写时参考的最近轮数，以及每轮结果在会话记忆中保留的行数 |
| `conversation.answer_max_rows` | 200 | 上一轮结果不超过这么多行时，针对结果的提问直接回答、不再查库 |
| `conversation.enable_clarify` / `conversation.contextualize_first_turn` | `true` / `false` | 信息不足时是否反问；首轮是否也经过改写（打开后首轮问题也能反问，多一次 LLM 调用） |
| `api.cors_origins` | `localhost:5173` | 允许跨域访问的前端地址 |

要接入自己的数仓，需要修改 `conf/meta_config.yaml`：声明要纳入的表（描述中写明粒度）和字段（角色、描述、别名、是否同步取值，外键还要写明关联目标 `ref`），以及指标（描述、计算公式、依赖字段），然后重新构建知识库。构建前会自动检查 `ref` 和指标依赖的字段是否都存在。

## 开发

```bash
uv run ruff check --fix .   # 代码检查
uv run ruff format .        # 格式化
uv run pytest               # 单元测试
uv run python -m evals.check_dataset   # 在数仓上校验评测集与示例
uv run python -m evals.runner          # 问数 Agent 答题打分（需要 LLM）
uv run python -m evals.runner --multiturn teacher   # 多轮追问评测（free 为整段自由对话）
uv run python -m evals.runner --llm-cache evals/.cache/glm-5.3-flash.sqlite   # 回放仓库里的录制缓存，不调用 LLM
cd frontend && pnpm lint    # 前端类型检查
```

`notebooks/` 下有三本 notebook，依赖不在默认安装里，先运行 `uv sync --group notebook`：

- `dw_explore.ipynb`：探查数仓，包括表与字段、指标口径速算、对账与外键自检、常见分析写法
- `eval_tutorial.ipynb`：评测教程，讲评测的思路、判分规则、归因和回归门禁，并解读 `evals/results/glm-5.3-flash/` 里的真实评测结果
- `eval_code_walkthrough.ipynb`：评测代码精读，逐个文件讲 `evals/` 的设计和工程手法，带着问题重读真实结果（置信区间、配对比较、失败是否独立、分数是否依赖运行日期），并列出精读中发现的评测代码问题和练习；不需要 LLM key

架构说明、开发约定和已知问题见 [CLAUDE.md](CLAUDE.md)。

## 当前限制与规划

- **多轮会话没有持久化**：追问改写、基于结果回答、反问澄清都已支持，但会话保存在进程内存里，服务重启即丢失，也没有会话列表和历史加载；首轮问题默认不会反问。
- **评测**：已有 glm-5.3-flash 的基线（单轮 91%，原问法 96%、换问法 86%；多轮自由对话的追问轮 96%、整段对话成功率 91%），结果和解读见 `notebooks/eval_tutorial.ipynb`；还没有接入 CI。评测代码本身有几处已知缺陷（基线门禁对基线里已失败的必过题也判退化、模型 SQL 里的 `CURDATE()` 让判分依赖运行日期等），见 `notebooks/eval_code_walkthrough.ipynb` 第 11 节，尚未修复。
- **生产化能力**：权限控制、多租户、查询缓存、限流、监控告警等暂未涉及。

开源项目借鉴评估、多轮问数（基于 LangGraph checkpointer）方案和后续路线见 [docs/design/borrowing-and-roadmap.md](docs/design/borrowing-and-roadmap.md)。

## 致谢与许可证

本项目基于 [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent) 二次开发。原项目整理自尚硅谷「大模型智能体掌柜问数」项目。

以 [MIT License](LICENSE) 开源。
