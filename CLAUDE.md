# CLAUDE.md

本文件为 Claude Code 在本仓库中工作时提供指引。

## 项目定位

「电商问数」是一个 NL2SQL 智能问数 Agent：用户用自然语言提问，系统先从元数据知识库召回相关字段、指标、字段取值，再由 LLM 分步过滤上下文、生成 SQL、校验、修正并在教学数仓上执行，过程通过 SSE 流式推给前端。

- 后端：Python 3.14 + FastAPI + LangGraph + LangChain + SQLAlchemy(async)，依赖用 `uv` 管理
- 前端：React 19 + Vite + Tailwind CSS 3 + TypeScript，依赖用 `pnpm` 管理（位于 `frontend/`）
- 基础设施（`docker/docker-compose.yaml`）：MySQL 8（`meta` 元数据库 + `dw` 教学数仓）、Qdrant、Elasticsearch 8.19 + IK 分词、Kibana、TEI Embedding（`bge-large-zh-v1.5`，1024 维）
- 本仓库 fork 自教程项目 `didilili/shopkeeper-agent`，这里只有 `main`，README 中提到的章节分支（`03-env-services` 等）在上游仓库
- 没有测试、没有 CI；代码注释、日志、Prompt、前端文案全部是中文

## 常用命令

```bash
# 后端
uv sync                                        # 安装依赖（会自动拉取 Python 3.14）
cp .env.example .env                           # 填入 LLM_API_KEY（必需，见下方“陷阱”）
docker compose -f docker/docker-compose.yaml up -d
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml   # 构建元数据知识库
uv run fastapi dev main.py                     # 启动后端，POST http://127.0.0.1:8000/api/query

uv run ruff check --fix .                      # lint（规则 E/F/I，忽略 E501）
uv run ruff format .                           # 格式化（行宽 88）

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
- **Qdrant**：`column_info_collection`、`metric_info_collection`。每个字段/指标拆成多个 point（名称、描述、每个别名各一个），payload 是完整实体
- **Elasticsearch** `value_index`：只同步 `sync: true` 的字段的全部去重取值，`value` 用 `ik_max_word` 分词

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
  ─ generate_sql ─ validate_sql ─┬─(error is None)─────────── run_sql ─ END
                                 └─(有 error)─ correct_sql ──┘
```

| 节点 | 做什么 | 依赖 |
| --- | --- | --- |
| `extract_keywords` | jieba TF-IDF + 词性过滤，并把原始问题也加入关键词 | 无 |
| `recall_column` / `recall_metric` | LLM 扩展检索词 → 逐词 Embedding → Qdrant（阈值 0.6，top 20）→ 按 id 去重 | LLM、TEI、Qdrant |
| `recall_value` | LLM 扩展取值候选 → ES `match` 检索 → 按 id 去重 | LLM、ES |
| `merge_retrieved_info` | 以 column_id 合并三路结果；补齐指标依赖字段、把命中取值并入 `examples`、为每张候选表补齐主外键；产出 `table_infos` / `metric_infos` | Meta MySQL |
| `filter_table` / `filter_metric` | LLM 只返回“保留哪些”（JSON），程序按名字裁剪原结构 | LLM |
| `add_extra_context` | 当前日期/星期/季度 + 数据库方言/版本 | DW MySQL |
| `generate_sql` | 上下文以 YAML 形式塞进 Prompt，输出纯 SQL 文本 | LLM |
| `validate_sql` | `EXPLAIN <sql>`，失败不抛异常，把错误写进 `state["error"]` | DW MySQL |
| `correct_sql` | 带完整上下文 + 报错做最小修正，覆盖 `state["sql"]` | LLM |
| `run_sql` | 执行 SQL，结果只通过 stream writer 发出，**不写回 state** | DW MySQL |

一次查询约 6～7 次 LLM 调用（3 次检索词扩展、2 次过滤、1 次生成，可能再加 1 次修正）。

### State 与 Context

- `DataAgentState`（`app/agent/state.py`，TypedDict，**没有任何 reducer**）：节点间传递的业务数据。并行节点必须写互不相同的 key，否则 LangGraph 会报并发更新冲突；要让多个并行节点写同一个 key，需要给该字段加 `Annotated[..., reducer]`
- `DataAgentContext`（`app/agent/context.py`）：仓储、Embedding 客户端等外部依赖，节点通过 `runtime.context[...]` 取用，不放进 State
- LLM 是 `app/agent/llm.py` 的模块级单例（`init_chat_model(model_provider="openai")`，`temperature=0`），节点直接 `import llm`，换 OpenAI 兼容平台只改 `conf/app_config.yaml` 的 `llm` 段

### SSE 协议（前后端契约）

节点通过 `runtime.stream_writer` 写 dict，`graph.astream(stream_mode="custom")` 取出后由 `QueryService` 包成 `data: {json}\n\n`：

```json
{"type": "progress", "step": "生成SQL", "status": "running|success|error"}
{"type": "result", "data": [{"列名": "值"}]}
{"type": "error", "message": "..."}
```

`step` 的**中文名就是协议的一部分**：`frontend/src/components/StepRail.tsx` 里的 `nodes` 数组按这个字符串匹配节点状态，坐标和连线（`connectors`）也是手写的。前端类型定义在 `frontend/src/types/agent.ts`，解析在 `frontend/src/lib/agentApi.ts`。

### 标识与数据约定

- 表 id = 表名；字段 id = `表名.字段名`；指标 id = 指标名；取值 id = `表名.字段名.取值`
- 字段 `role`：`primary_key` / `foreign_key` / `dimension` / `measure`；表 `role`：`fact` / `dim`
- 教学数仓是星型模型：`fact_order`（115 行）+ `dim_region` / `dim_customer` / `dim_product` / `dim_date`。**数据只覆盖 2025-01-01 ～ 2025-03-31**；`dim_date.date_id` 是 `yyyyMMdd` 的 INT，`quarter` 是 `'Q1'` 这样的字符串

## 开发约定

- 每个 `.py` 文件以中文模块 docstring 开头说明职责，函数有一句话 docstring，行内注释解释“为什么”。新代码保持这个风格
- 节点统一写法：`async def node(state: DataAgentState, runtime: Runtime[DataAgentContext])`；开头发 `running`，成功发 `success`，`except` 里 `logger.error` + 发 `error` 后 `raise`；返回只包含本节点更新的 key 的 dict
- 日志统一 `from app.core.log import logger`（loguru，自动带 request_id），不要用 `print` 或标准库 `logging`
- Prompt 放 `prompts/<name>.prompt`，用 `load_prompt("<name>")` 读取、`PromptTemplate` 渲染。模板里的字面量花括号要写成 `{{ }}`（见 `filter_table_info.prompt`）
- 需要结构化输出的节点用 `JsonOutputParser`，Prompt 里明确“仅输出 JSON”；让 LLM 只做选择，结构裁剪由程序完成
- 给 LLM 的结构化上下文一律 `yaml.dump(..., allow_unicode=True, sort_keys=False)`
- 配置项要同时改 `conf/app_config.yaml` 和 `app/conf/app_config.py` 的 dataclass（OmegaConf 结构化校验，缺字段或多字段都会在导入时报错）；敏感值走 `.env` + `${oc.env:VAR}`
- 前端：函数组件 + hooks，无状态管理库和路由；样式只用 Tailwind 工具类，主题色（`parchment` / `ink` / `moss` / `brass` / `tomato`）定义在 `tailwind.config.ts`

## 常见改动要动哪些文件

- **新增图节点**：`app/agent/nodes/<node>.py` → `graph.py` 注册节点和边 → 需要的新状态加到 `state.py` → 新外部依赖加到 `context.py`、`api/dependencies.py`、`services/query_service.py`，以及 `graph.py` 末尾的本地调试代码 → `StepRail.tsx` 的 `nodes` / `connectors` 加上同名步骤（否则前端不显示该步骤）
- **新增 SSE 事件类型**：节点里 `writer({...})` → `frontend/src/types/agent.ts` 的 `AgentEvent` → `App.tsx` 的 `onEvent`（当前非 progress/result 的事件都会被当作 error 处理）
- **数仓加表/字段/指标**：改 `docker/mysql/dw.sql`（只在 MySQL 卷首次初始化时执行）→ 改 `conf/meta_config.yaml` → 重建知识库（见下条陷阱）
- **Meta 库加表**：`docker/mysql/meta.sql` + `app/models/` + `app/entities/` + mapper + `MetaMySQLRepository`。没有迁移工具，表结构以 `meta.sql` 为准
- **新增接口**：`app/api/routers/` 新建 router，在 `main.py` `include_router`

## 已知问题与陷阱

改动相关代码前先了解这些，它们是现状而不是设计目标：

**运行环境**
- 没有 `LLM_API_KEY` 时，任何 `import app.conf.app_config` 的模块（几乎全部）都会在导入阶段报 OmegaConf 插值错误。只做静态检查时可临时 `LLM_API_KEY=dummy uv run ...`
- 所有服务地址、MySQL 账号密码都硬编码在 `conf/app_config.yaml` 和 compose 文件里，默认全部是 `localhost`
- `main.py` 没有 CORS 中间件，前端靠 Vite 代理访问；若设置 `VITE_API_BASE_URL` 跨域直连需要先加 CORS

**知识库构建不幂等**
- 重跑 `build_meta_knowledge` 会在 Meta MySQL 主键冲突处失败（`add_all` 而非 upsert）；Qdrant point id 是随机 `uuid4`，重跑会产生重复向量并挤占 top 20 名额；只有 ES 是按 id 覆盖
- 重建前需清空 meta 库四张表并删除两个 Qdrant collection，或直接 `docker compose -f docker/docker-compose.yaml down -v` 后重新初始化

**SQL 闭环**
- `correct_sql` 之后直接进 `run_sql`，**不会再校验**，只有一次修正机会；修正后仍错会在 `run_sql` 抛异常，前端收到 `error` 事件
- SQL 只读仅靠 Prompt 约束，执行前没有任何程序侧检查，且 `didilili` 账号对 `dw` 有全部权限。DML 因为从不 commit 会随 Session 关闭回滚，但 DDL（`DROP` 等）在 MySQL 中隐式提交
- `validate_sql` 校验失败时发的也是 `status: "success"`（表示“校验这一步跑完了”），是否有错只体现在 `state["error"]`
- `run_sql` 不把结果写回 state，也没有行数上限；要在其后追加节点（总结、图表等）需要先让它返回结果
- `add_extra_context` 注入的是真实当前日期，而数据只有 2025 年 Q1，“本月”“最近 7 天”这类相对时间问题会查出空结果

**检索**
- ES 的 `min_score=0.6` 是未归一化的 BM25 分数，和 Qdrant 的余弦阈值 0.6 不是同一量纲
- `conf/app_config.yaml` 的 `es.index_name` 没有被使用，实际索引名是 `ValueESRepository.index_name = "value_index"`；两个 Qdrant collection 名同样硬编码在仓储类上
- `meta_config.yaml` 中指标 `AOV` 的 `relevant_columns` 写的是 `fact_order.order_quantity`，按其描述（订单金额平均值）应为 `order_amount`，疑似配置错误
- `recall_*` 节点对每个关键词串行调用 Embedding / 检索

**其他**
- `filter_table` 就地修改了 state 中的 `table_info["columns"]`
- 请求级 `AsyncSession` 不支持并发使用。目前并行的 `recall_*` 节点不碰 MySQL 所以没问题；新增会访问 MySQL 的并行节点时要注意
- 整体是单轮无状态：图没有 checkpointer，接口不带会话 id，前端的消息列表只存在内存里
- 前端 `upsertStep` 以步骤名为 key 覆盖状态，同一节点若执行多次（例如加入校验-修正循环）只会显示最后一次
- README 的“能力边界”一节列出了上游有意不做的方向（权限、多租户、SQL 审计、缓存限流、评测、多轮对话等）
