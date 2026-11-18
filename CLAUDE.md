# CLAUDE.md

本文件为 Claude Code 在本仓库中工作时提供指引。

## 项目定位

「电商问数」是一个 NL2SQL 智能问数 Agent：用户用自然语言提问，系统先从元数据知识库召回相关字段、指标、字段取值，再由 LLM 分步过滤上下文、生成 SQL、校验、修正并在教学数仓上执行，过程通过 SSE 流式推给前端。

- 后端：Python 3.14 + FastAPI + LangGraph + LangChain + SQLAlchemy(async)，依赖用 `uv` 管理
- 前端：React 19 + Vite + Tailwind CSS 3 + TypeScript，依赖用 `pnpm` 管理（位于 `frontend/`）
- 基础设施（`docker/docker-compose.yaml`）：MySQL 8（`meta` 元数据库 + `dw` 教学数仓）、Qdrant、Elasticsearch 8.19 + IK 分词、Kibana、TEI Embedding（`bge-large-zh-v1.5`，1024 维）
- 本仓库 fork 自教程项目 `didilili/shopkeeper-agent`，这里只有 `main`，README 中提到的章节分支（`03-env-services` 等）在上游仓库
- 只有少量单元测试（`tests/`），没有 CI 和评测集；代码注释、日志、Prompt、前端文案全部是中文
- 后续改进方向（评测体系、多轮问数）的设计方案见 `docs/design/eval-and-multiturn.md`

## 常用命令

```bash
# 后端
uv sync                                        # 安装依赖（会自动拉取 Python 3.14）
cp .env.example .env                           # 填入 LLM_API_KEY（必需，见下方“陷阱”）
docker compose -f docker/docker-compose.yaml up -d
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml   # 构建元数据知识库（幂等，可重复运行）
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml --reset  # 先清空全部知识库再构建
uv run fastapi dev main.py                     # 启动后端，POST http://127.0.0.1:8000/api/query

uv run ruff check --fix .                      # lint（规则 E/F/I，忽略 E501）
uv run ruff format .                           # 格式化（行宽 88）
uv run pytest                                  # 单元测试（不依赖外部服务）

# 单独调试
uv run python -m app.agent.graph               # 不经 HTTP 跑一遍完整问数图（问题写死在文件末尾）
uv run python -m app.agent.llm                 # 验证 LLM 配置
uv run python -m app.clients.mysql_client_manager      # 各 client manager 都带 __main__ 冒烟脚本
uv run python -m app.clients.embedding_client_manager

# 前端
cd frontend && pnpm install
pnpm dev                                       # http://localhost:5173，/api 代理到 127.0.0.1:8000
pnpm lint                                      # 仅 tsc --noEmit，没有 ESLint
pnpm build
```

Embedding 模型需要先下载到挂载目录（已被 gitignore）：
`uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5`

提交前可选 `pre-commit`（ruff check --fix + ruff format + 基础文件检查）。

## 架构

### 两条主线

| 主线 | 入口 | 核心 |
| --- | --- | --- |
| 离线：元数据知识库构建 | `app/scripts/build_meta_knowledge.py` | `MetaKnowledgeService.build()` 读取 `conf/meta_config.yaml` |
| 在线：自然语言问数 | `main.py` → `app/api/routers/query_router.py` | `QueryService.query()` 驱动 `app/agent/graph.py` |

离线构建把 `meta_config.yaml` 里声明的表/字段/指标写入三处：

- **Meta MySQL**（`table_info` / `column_info` / `metric_info` / `column_metric`）：权威的结构化元数据。字段的 `type` 和 `examples`（10 个去重样例值）是构建时从 DW 实查补齐的
- **Qdrant**：`column_info_collection`、`metric_info_collection`（名字来自配置）。每个字段/指标拆成多个 point（名称、描述、每个别名各一个），payload 是完整实体，point id 由 `实体 id + 文本` 经 uuid5 确定性生成
- **Elasticsearch** `value_index`（名字来自配置）：只同步 `sync: true` 的字段的全部去重取值（跳过 NULL 和空串），`value` 用 `ik_max_word` 分词

构建是幂等的：配置里出现的表/指标会整体替换——先删这些表的旧字段、这些指标的旧依赖、对应实体的旧向量点和旧取值，再写入。配置里**整张删掉**的表或指标不会被自动清理，需要 `--reset`。

### 分层与依赖方向

```
api (router/schemas/dependencies/lifespan)
  └─ services (QueryService / MetaKnowledgeService)
       ├─ agent (graph / state / context / nodes / llm)
       └─ repositories (mysql/meta, mysql/dw, qdrant, es)
            ├─ clients (*_client_manager：模块级单例，显式 init()/close())
            ├─ models (SQLAlchemy ORM，仅 meta 库)  ←mappers→  entities (dataclass 业务实体)
            └─ conf (OmegaConf + dataclass，导入即加载)
```

- Repository 之上只流转 `entities`，ORM 模型不出仓储层，转换走 `repositories/mysql/meta/mappers/`
- 事务边界由 Service 决定（`async with repo.session.begin()`），Repository 的 `save_*` 只 `add_all`
- 客户端是应用级单例（`lifespan` 里 init/close）；MySQL Session 是请求级（`dependencies.py` 用 `Depends` 组装），Repository 每次请求新建

### 问数图（`app/agent/graph.py`）

```
extract_keywords ─┬─ recall_column ─┐
                  ├─ recall_value  ─┼─ merge_retrieved_info ─┬─ filter_table  ─┐
                  └─ recall_metric ─┘                        └─ filter_metric ─┴─ add_extra_context
  ─ generate_sql ─ validate_sql ─┬─(error is None)─ run_sql ─ END
                    ▲            └─(有 error)─ correct_sql ─┐
                    └───────────────────────────────────────┘   修正次数用完时 validate_sql 抛异常结束
```

| 节点 | 做什么 | 依赖 |
| --- | --- | --- |
| `extract_keywords` | jieba TF-IDF + 词性过滤，并把原始问题也加入关键词 | 无 |
| `recall_column` / `recall_metric` | LLM 扩展检索词 → 批量 Embedding → 并发查 Qdrant（余弦阈值、top N 来自配置）→ 按 id 去重 | LLM、TEI、Qdrant |
| `recall_value` | LLM 扩展取值候选 → 并发 ES `match` 检索（BM25 `min_score` 来自配置）→ 按 id 去重 | LLM、ES |
| `merge_retrieved_info` | 以 column_id 合并三路结果；补齐指标依赖字段、把命中取值并入 `examples`、为每张候选表补齐主外键；产出 `table_infos` / `metric_infos` | Meta MySQL |
| `filter_table` / `filter_metric` | LLM 只返回“保留哪些”（JSON），程序按名字裁剪出新结构 | LLM |
| `add_extra_context` | 日期/星期/季度（`agent.today` 配置了就用它，否则用真实日期）+ 数据库方言/版本 | DW MySQL |
| `generate_sql` | 上下文以 YAML 形式塞进 Prompt，`clean_sql` 清洗后写入 `sql`，并把 `correct_attempts` 置 0 | LLM |
| `validate_sql` | 只读校验（sqlglot）+ `EXPLAIN`；失败时把数据库原始报错写进 `error` 并发 `status: error`；修正次数达到 `agent.max_correct_attempts` 时抛异常 | DW MySQL |
| `correct_sql` | 带完整上下文 + 报错做最小修正，覆盖 `sql`，`correct_attempts + 1`，然后回到 `validate_sql` | LLM |
| `run_sql` | 只读校验后执行，最多 `agent.max_result_rows` 行、超时 `agent.max_execution_ms`；结果写回 `result` / `truncated` 并发 `result` 事件 | DW MySQL |

一次查询约 6 次 LLM 调用（3 次检索词扩展、2 次过滤、1 次生成），每次修正再加 1 次。

### State 与 Context

- `DataAgentState`（`app/agent/state.py`，TypedDict，**没有任何 reducer**）：节点间传递的业务数据。并行节点必须写互不相同的 key，否则 LangGraph 会报并发更新冲突；要让多个并行节点写同一个 key，需要给该字段加 `Annotated[..., reducer]`
- `DataAgentContext`（`app/agent/context.py`）：仓储、Embedding 客户端等外部依赖，节点通过 `runtime.context[...]` 取用，不放进 State
- LLM 是 `app/agent/llm.py` 的模块级单例（`init_chat_model(model_provider="openai")`，`temperature=0`），节点直接 `import llm`，换 OpenAI 兼容平台只改 `conf/app_config.yaml` 的 `llm` 段

### SSE 协议（前后端契约）

节点通过 `runtime.stream_writer` 写 dict，`graph.astream(stream_mode="custom")` 取出后由 `QueryService` 包成 `data: {json}\n\n`：

```json
{"type": "progress", "step": "校验SQL", "status": "running|success|error", "detail": "可选，失败原因"}
{"type": "result", "data": [{"列名": "值"}], "sql": "最终 SQL", "truncated": false, "max_rows": 1000}
{"type": "error", "message": "..."}
```

`step` 的**中文名就是协议的一部分**：`frontend/src/components/StepRail.tsx` 里的 `nodes` 数组按这个字符串匹配节点状态，坐标和连线（`connectors`）也是手写的。前端 `upsertStep` 按步骤名记录最新状态，并统计进入 `running` 的次数（循环中的步骤显示 `×N`）。前端类型定义在 `frontend/src/types/agent.ts`，解析在 `frontend/src/lib/agentApi.ts`。

### 标识与数据约定

- 表 id = 表名；字段 id = `表名.字段名`；指标 id = 指标名；取值 id = `表名.字段名.取值`
- 字段 `role`：`primary_key` / `foreign_key` / `dimension` / `measure`；表 `role`：`fact` / `dim`
- 教学数仓是星型模型：`fact_order`（115 行，一行一个订单）+ `dim_region` / `dim_customer` / `dim_product` / `dim_date`。**数据只覆盖 2025-01-01 ～ 2025-03-31**（所以 `agent.today` 默认固定为 `2025-03-31`）；`dim_date.date_id` 是 `yyyyMMdd` 的 INT，`quarter` 是 `'Q1'` 这样的字符串
- 指标口径（`conf/meta_config.yaml`，description 里写明公式，会原样进入 SQL 生成 Prompt）：GMV = `SUM(order_amount)`；AOV（平均订单金额）= `SUM(order_amount) / COUNT(DISTINCT order_id)`；ASP（件单价）= `SUM(order_amount) / SUM(order_quantity)`

## 开发约定

- 每个 `.py` 文件以中文模块 docstring 开头说明职责，函数有一句话 docstring，行内注释解释“为什么”。新代码保持这个风格
- 节点统一写法：`async def node(state: DataAgentState, runtime: Runtime[DataAgentContext])`；开头发 `running`，成功发 `success`，`except` 里 `logger.error` + 发 `error` 后 `raise`；返回只包含本节点更新的 key 的 dict
- 日志统一 `from app.core.log import logger`（loguru，自动带 request_id），不要用 `print` 或标准库 `logging`
- Prompt 放 `prompts/<name>.prompt`，用 `load_prompt("<name>")` 读取、`PromptTemplate` 渲染。模板里的字面量花括号要写成 `{{ }}`（见 `filter_table_info.prompt`）
- 需要结构化输出的节点用 `JsonOutputParser`，Prompt 里明确“仅输出 JSON”；让 LLM 只做选择，结构裁剪由程序完成
- 给 LLM 的结构化上下文一律 `yaml.dump(..., allow_unicode=True, sort_keys=False)`
- 任何要在数仓上执行的 SQL 都必须经过 `app/core/sql_safety.py` 的 `ensure_readonly`（`DWMySQLRepository.validate/run` 已内置）；LLM 产出的 SQL 先过 `clean_sql`
- 检索阈值、集合/索引名、SQL 闭环参数等可调项放进 `conf/app_config.yaml`，不要在代码里写死
- 改了 `app/core/` 下的纯函数要补 `tests/` 里的 pytest 用例
- 配置项要同时改 `conf/app_config.yaml` 和 `app/conf/app_config.py` 的 dataclass（OmegaConf 结构化校验，缺字段或多字段都会在导入时报错）；敏感值走 `.env` + `${oc.env:VAR}`
- 前端：函数组件 + hooks，无状态管理库和路由；样式只用 Tailwind 工具类，主题色（`parchment` / `ink` / `moss` / `brass` / `tomato`）定义在 `tailwind.config.ts`

## 常见改动要动哪些文件

- **新增图节点**：`app/agent/nodes/<node>.py` → `graph.py` 注册节点和边 → 需要的新状态加到 `state.py` → 新外部依赖加到 `context.py`、`api/dependencies.py`、`services/query_service.py`，以及 `graph.py` 末尾的本地调试代码 → `StepRail.tsx` 的 `nodes` / `connectors` 加上同名步骤（否则前端不显示该步骤）。要在 `run_sql` 之后接节点（总结、图表等），直接读 `state["result"]`
- **新增 SSE 事件类型**：节点里 `writer({...})` → `frontend/src/types/agent.ts` 的 `AgentEvent` → `App.tsx` 的 `onEvent`（当前非 progress/result 的事件都会被当作 error 处理）
- **数仓加表/字段/指标**：改 `docker/mysql/dw.sql`（只在 MySQL 卷首次初始化时执行）→ 改 `conf/meta_config.yaml` → 重跑知识库构建（删除了表或指标时加 `--reset`）
- **新增配置项**：`conf/app_config.yaml` + `app/conf/app_config.py` 的 dataclass 两处同时改
- **Meta 库加表**：`docker/mysql/meta.sql` + `app/models/` + `app/entities/` + mapper + `MetaMySQLRepository`。没有迁移工具，表结构以 `meta.sql` 为准
- **新增接口**：`app/api/routers/` 新建 router，在 `main.py` `include_router`

## 已知问题与陷阱

改动相关代码前先了解这些，它们是现状而不是设计目标：

**运行环境（暂不处理）**
- 没有 `LLM_API_KEY` 时，任何 `import app.conf.app_config` 的模块（几乎全部）都会在导入阶段报 OmegaConf 插值错误。只做静态检查时可临时 `LLM_API_KEY=dummy uv run ...`；`tests/` 下的测试刻意不导入配置
- 所有服务地址、MySQL 账号密码都硬编码在 `conf/app_config.yaml` 和 compose 文件里，默认全部是 `localhost`；数仓账号 `didilili` 对 `dw` 有全部权限，只读完全靠程序侧的 `ensure_readonly`

**SQL 闭环**
- `ensure_readonly` 基于 sqlglot 的 MySQL 方言解析，sqlglot 解析不了的合法 MySQL 语法会被当作“无法解析”拒绝，然后进入修正流程
- 行数上限用 `fetchmany` 实现，asyncmy 默认缓冲整个结果集，超大结果仍会先完整读进内存；靠 `max_execution_ms` 兜底
- `agent.today` 是全局配置，不是每次请求的参数；评测（每题指定日期）和多轮会话需要把它改为请求级

**检索**
- ES `min_score` 是 BM25 原始得分，2.0 是在教学数仓上实测标定的（正确命中最低约 3.7），数据量或取值分布变化后需要重新标定；Qdrant 余弦阈值 0.6 没有标定过（本地测试拿不到真实 bge 模型）
- `extract_keywords` 用 `set` 去重，关键词顺序不稳定（影响日志可读性和 LLM 缓存命中）

**其他**
- `filter_table` / `filter_metric` / `recall_*` 用 `JsonOutputParser`，模型输出格式不对会直接抛异常中断整条流程，没有重试或降级
- 请求级 `AsyncSession` 不支持并发使用。目前并行的 `recall_*` 节点不碰 MySQL 所以没问题；新增会访问 MySQL 的并行节点时要注意
- 整体是单轮无状态：图没有 checkpointer，接口不带会话 id，前端的消息列表只存在内存里（改进方案见 `docs/design/eval-and-multiturn.md`）
- README 的“能力边界”一节列出了上游有意不做的方向（权限、多租户、SQL 审计、缓存限流、评测、多轮对话等）

## 本地完整联调的注意事项

- Docker Hub 偶尔限流，可改用 `mirror.gcr.io/library/<image>` 拉取后 `docker tag` 成 compose 里的名字
- ES 节点磁盘使用率超过 90% 时会拒绝分配分片（集群 red，构建报 503）；本地开发可执行
  `curl -XPUT localhost:9200/_cluster/settings -H 'content-type: application/json' -d '{"persistent":{"cluster.routing.allocation.disk.threshold_enabled":false}}'`
- ES 的 IK 插件 zip 必须和 ES 版本完全一致（当前 8.19.10）
