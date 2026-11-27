# CLAUDE.md

本文件为 Claude Code 在本仓库中工作时提供指引。

## 项目定位

「电商问数」是一个 NL2SQL 智能问数 Agent：用户用自然语言提问，系统先从元数据知识库召回相关字段、指标、字段取值，再由 LLM 分步过滤上下文、生成 SQL、校验、修正并在教学数仓上执行，过程通过 SSE 流式推给前端。同一会话里的追问会先结合上文改写成独立问题再走单轮流程；针对上一轮结果的提问直接基于结果回答，信息不足时先反问用户（多轮会话图）。

- 后端：Python 3.14 + FastAPI + LangGraph + LangChain + SQLAlchemy(async)，依赖用 `uv` 管理
- 前端：React 19 + Vite + Tailwind CSS 3 + TypeScript，依赖用 `pnpm` 管理（位于 `frontend/`）
- 基础设施（`docker/docker-compose.yaml`）：MySQL 8（`meta` 元数据库 + `dw` 教学数仓）、Qdrant、Elasticsearch 8.19 + IK 分词、Kibana、TEI Embedding（`bge-large-zh-v1.5`，1024 维）
- 本仓库基于 `didilili/shopkeeper-agent` 二次开发，只有 `main` 分支；上游的教程章节分支不在这里
- 有单元测试（`tests/`）、业务口径陷阱评测集、多轮追问评测集与评测 runner（`evals/`），还没有 CI；代码注释、日志、Prompt、前端文案全部是中文
- 设计文档：`docs/design/dw-schema-v2.md`（数仓）、`eval-and-multiturn.md`（评测与多轮初版方案）、`borrowing-and-roadmap.md`（开源借鉴评估、多轮 checkpointer 方案与实施进度、后续路线，**最新**）

## 常用命令

```bash
# 后端
uv sync                                        # 安装依赖（会自动拉取 Python 3.14）
cp .env.example .env                           # 填入 LLM_API_KEY（必需，见下方“陷阱”）
docker compose -f docker/docker-compose.yaml up -d
uv run python -m app.scripts.generate_dw_data  # 重建 dw 的表并写入生成数据（固定种子，约 17 秒；--scale small 更快）
uv run python -m app.scripts.export_dw_sql     # 导出固定 SQL 快照 docker/snapshot/dw_snapshot.sql.gz（不连库；--database 换库名）
gunzip -c docker/snapshot/dw_snapshot.sql.gz | docker exec -i mysql mysql -uroot -pdili123   # 不用 Python 直接导入同样的数据
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml   # 构建元数据知识库（幂等，可重复运行；同时写入业务术语和 conf/sql_examples.yaml 的示例）
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml --reset  # 先清空全部知识库再构建
uv run fastapi dev main.py                     # 启动后端，POST http://127.0.0.1:8000/api/query

uv run ruff check --fix .                      # lint（规则 E/F/I，忽略 E501）
uv run ruff format .                           # 格式化（行宽 88）
uv run pytest                                  # 单元测试（不依赖外部服务，含评测集静态检查）
uv run python -m evals.check_dataset           # 在数仓上校验评测题与 few-shot 示例：标准 SQL 有结果、错误写法结果不同、示例无泄漏（需 MySQL）
uv run python -m evals.runner                  # 问数 Agent 答题打分（需全部服务和 LLM）；--predictor gold/trap 自检打分逻辑，见 evals/runner.py 文件头
uv run python -m evals.runner --multiturn teacher   # 多轮评测（默认 evals/datasets/multiturn.yaml）；free 为整段自由对话
uv run python -m evals.check_dataset -d evals/datasets/multiturn.yaml --per-category 0   # 校验多轮评测题（每一轮都检查）

# 单独调试
uv run python -m app.agent.graph               # 不经 HTTP 跑一遍完整问数图（问题写死在文件末尾）
uv run python -m app.agent.session_graph       # 不经 HTTP 在同一会话里连续问两轮（问题写死在文件末尾）
uv run python -m app.agent.llm                 # 验证 LLM 配置
uv run python -m app.clients.mysql_client_manager      # 各 client manager 都带 __main__ 冒烟脚本
uv run python -m app.clients.embedding_client_manager

# notebook（数仓探查、评测教程）
uv sync --group notebook                       # 安装 pandas、ipykernel、mysql-connector（不在默认的 dev 组里）

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
| 在线：自然语言问数 | `main.py` → `app/api/routers/query_router.py` | `QueryService.query()` 按 `thread_id` 驱动 `app/agent/session_graph.py`，其中 `run_pipeline` 调用单轮图 `app/agent/graph.py` |

离线构建把 `meta_config.yaml` 里声明的表/字段/指标/术语，以及 `conf/sql_examples.yaml` 的已验证问答示例写入三处（构建前先用 `validate_meta_config` 检查：外键必须有 `ref` 且指向存在的字段、指标依赖的字段必须存在，不通过直接报错，不会清库）：

- **Meta MySQL**（`table_info` / `column_info` / `metric_info` / `column_metric`）：权威的结构化元数据。字段的 `type` 和 `examples`（10 个去重样例值，日期转 ISO 字符串、Decimal 转 float）是构建时从 DW 实查补齐的；外键的关联目标存在 `column_info.ref_column`（ORM 属性名 `ref`）
- **Qdrant**：`column_info_collection`、`metric_info_collection`（名字来自配置）。每个字段/指标拆成多个 point（名称、描述、每个别名各一个），payload 是完整实体，point id 由 `实体 id + 文本` 经 uuid5 确定性生成
- **Qdrant** `term_collection` / `sql_example_collection`：业务术语和已验证问答示例，都以配置文件为准整体替换（先删集合再写）；示例的 `tables` 是解析 SQL 得到的物理表
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
- 事务边界由 Service 决定（`async with repo.session.begin()`），Repository 的 `save_*` 只 `add_all` / `merge`，不提交
- 客户端是应用级单例（`lifespan` 里 init/close）；MySQL Session 是请求级（`dependencies.py` 用 `Depends` 组装），Repository 每次请求新建

### 多轮会话图（`app/agent/session_graph.py`）

```
START ─ contextualize ─┬─(clarify)──────────────── ask_clarification ─(interrupt / resume)─► contextualize
                       ├─(result_qa 且有 last_result)─ answer_from_result ─┬─(答出)───► remember ─ END
                       │                                                  └─(答不了)─► run_pipeline
                       └─(其他)──────────────────────────────────────────────────────► run_pipeline ─► remember
checkpointer（InMemorySaver）按 thread_id 保存，也是 interrupt 暂停 / 恢复的前提
```

| 节点 | 做什么 |
| --- | --- |
| `contextualize` | 每轮先清空本轮字段（sql / result_preview / error / answer）；结合最近 `conversation.history_turns` 轮历史，把本轮输入改写成独立问题并判断意图（`new` / `followup` / `result_qa` / `clarify`，常量在 `app/core/conversation.py`），非 clarify 时发 `rewrite` 事件；**首轮默认跳过 LLM**（`conversation.contextualize_first_turn` 打开后首轮也改写，才能对首轮反问）；用户回答过反问时把回答以“（补充：……）”拼在原话后再改写，且不允许再次 clarify；LLM 输出不合法时退回“原话当新问题”并发 `status: error`，不中断。同时确定会话的 `today` |
| `ask_clarification` | `interrupt({"question", "options"})` 暂停；`Command(resume=回答)` 恢复后写 `clarification`、`clarified=True`，回到 `contextualize`。恢复时本节点会**重新执行**，`interrupt()` 之前的代码跑两次（分属提问和回答两个请求的事件流） |
| `answer_from_result` | `result_qa` 且 `last_result` 存在时，用上一轮完整结果（≤ `conversation.answer_max_rows` 行、未截断）让 LLM 直接回答（JSON `{answerable, answer}`），发 `answer` 事件；答不了、格式不对或报错时 `answer=None`，退回 `run_pipeline` |
| `run_pipeline` | 以 `standalone` 和 `today` 调用单轮图（`stream_mode=["custom","values"]`），原样转发单轮图的事件；`followup` 时把最近一条有 SQL 的轮次的问题和 SQL 作为 `reference` 传给 `generate_sql`；结果不大时整份写进 `last_result`；单轮流程抛异常时发 `error` 事件、把错误记进本轮，**不向外抛**，会话照常进入 `remember` |
| `remember` | 追加一轮 `Turn`（原话、改写、意图、SQL、结果预览、直接回答、反问后的补充、错误、today） |

- **checkpointer 只挂在会话图上**：单轮图用 `compile(checkpointer=False)` 编译，作为子图被调用时也不会继承外层 checkpointer（不写 `run_pipeline:*` 命名空间的检查点）。不要去掉这个参数，否则召回结果、表结构等大对象每一步都会写进检查点
- `SessionState.history` 有 `operator.add` reducer，只追加；其余字段是“本轮”数据，由 `contextualize` 在每轮开头清空（`clarified` / `clarification` 由 `QueryService` 的新一轮输入重置），新增本轮字段时也要每轮清空，否则会串到下一轮；`last_result` 例外，只由 `run_pipeline` 更新，跨轮保留
- 会话历史里每轮只存结果预览（`summarize_result`：列名、行数、前 `conversation.result_preview_rows` 行，值转成基础类型）；完整结果只保留最近一次查库的（`last_result`），给 `answer_from_result` 用
- **参考 SQL 不改变单轮提示词**：`generate_sql.prompt` 里 `{reference}` 紧挨在示例和任务要求之间，为空串时提示词与原来逐字节相同，单轮基线和 LLM 缓存都不受影响（已用缓存验证 52/52 命中）
- 会话停在反问处时：带 `resume` 的请求从暂停处恢复；不带 `resume` 的新问题会丢弃这次反问、从 START 开始新的一轮；没有待回答的反问却带了 `resume` 时，`QueryService` 先用 `aget_state` 检查并返回错误事件（否则图会以空输入运行而报错）
- 客户端中途断开时本轮不会进入 `remember`，检查点停在 `run_pipeline` 之前；下一次带新输入调用会从 START 重新开始，不需要清理

### 单轮问数图（`app/agent/graph.py`）

```
extract_keywords ─┬─ recall_column    ─┐
                  ├─ recall_value     ─┤
                  ├─ recall_metric    ─┼─ merge_retrieved_info ─┬─ filter_table  ─┐
                  └─ recall_knowledge ─┘                        └─ filter_metric ─┴─ add_extra_context
  ─ generate_sql ─ validate_sql ─┬─(error is None)─ run_sql ─ END
                    ▲            └─(有 error)─ correct_sql ─┐
                    └───────────────────────────────────────┘   修正次数用完时 validate_sql 抛异常结束
```

| 节点 | 做什么 | 依赖 |
| --- | --- | --- |
| `extract_keywords` | jieba TF-IDF + 词性过滤，并把原始问题也加入关键词 | 无 |
| `recall_column` / `recall_metric` | LLM 扩展检索词 → 批量 Embedding → 并发查 Qdrant（余弦阈值、top N 来自配置）→ 按 id 去重 | LLM、TEI、Qdrant |
| `recall_value` | LLM 扩展取值候选 → 并发 ES `match` 检索（BM25 `min_score` 来自配置）→ 按 id 去重 | LLM、ES |
| `recall_knowledge` | 业务术语按名称/别名精确匹配（`scroll_all` 取全部术语）；按问题向量召回已验证问答示例（`knowledge.*` 配置，可关闭） | Qdrant、TEI |
| `merge_retrieved_info` | 以 column_id 合并各路结果；补齐指标依赖字段、术语定义里引用的字段（`columns_in_text`）、把命中取值并入 `examples`、示例 SQL 用到的表；用 `app/agent/join_path.py` 按外键 `ref` 关系图补上候选表之间缺失的中间表；为每张表补齐主外键；产出 `table_infos` / `metric_infos`（外键字段带 `ref`） | Meta MySQL |
| `filter_table` / `filter_metric` | LLM 只返回“保留哪些”（JSON），程序按名字裁剪出新结构 | LLM |
| `add_extra_context` | 日期/星期/季度（季度是整数，和 `dim_date.quarter` 一致；`agent.today` 配置了就用它，否则用真实日期）+ 数据库方言/版本 | DW MySQL |
| `generate_sql` | 上下文以 YAML 形式塞进 Prompt（含命中的术语和召回的示例），`clean_sql` 清洗后写入 `sql`，并把 `correct_attempts` 置 0 | LLM |
| `validate_sql` | 只读校验（sqlglot）+ `EXPLAIN` + 语义检查（`app/core/sql_lint.py`：扇出聚合、关联条件不符合 `ref`，`agent.semantic_check` 开关）；问题写进 `error` 并发 `status: error`。EXPLAIN 失败且修正次数用完时抛异常；**只剩语义问题时不抛，告警后照常执行**，问题留在 `lint_issues` | DW MySQL、Meta MySQL |
| `correct_sql` | 带完整上下文 + 报错做最小修正，覆盖 `sql`，`correct_attempts + 1`，然后回到 `validate_sql` | LLM |
| `run_sql` | 只读校验后执行，最多 `agent.max_result_rows` 行、超时 `agent.max_execution_ms`；结果写回 `result` / `truncated` 并发 `result` 事件 | DW MySQL |

一次查询约 6 次 LLM 调用（3 次检索词扩展、2 次过滤、1 次生成），每次修正再加 1 次（`recall_knowledge` 不调 LLM）；多轮会话中第 2 轮起再加 1 次改写；基于上一轮结果直接回答的轮次只有 2 次（改写 + 回答），反问的轮次在用户回答后再改写一次。

`today`：State 里的 `today`（接口可选字段）优先，其次 `agent.today` 配置，最后真实日期。多轮会话里首轮确定后整段会话沿用（存在 `SessionState.today`），请求显式传入时覆盖。

### State 与 Context

- `SessionState` / `Turn`（同在 `state.py`）：会话图的状态，见上一节；`history` 是唯一带 reducer 的字段
- `DataAgentState`（`app/agent/state.py`，TypedDict，**没有任何 reducer**）：单轮图节点间传递的业务数据。并行节点必须写互不相同的 key，否则 LangGraph 会报并发更新冲突；要让多个并行节点写同一个 key，需要给该字段加 `Annotated[..., reducer]`
- `DataAgentContext`（`app/agent/context.py`）：仓储、Embedding 客户端等外部依赖，节点通过 `runtime.context[...]` 取用，不放进 State（也就不进检查点）；会话图和单轮图共用同一个 context。组装统一走 `QueryService.from_sessions(meta_session, dw_session).build_context()`（调试入口和评测 runner 都用它），HTTP 接口走 `api/dependencies.py`
- LLM 是 `app/agent/llm.py` 的模块级单例（`init_chat_model(model_provider="openai")`，`temperature=0`），节点直接 `import llm`；默认是智谱开放平台的 `glm-5.3-flash`（`https://open.bigmodel.cn/api/paas/v4/`），换 OpenAI 兼容平台只改 `conf/app_config.yaml` 的 `llm` 段

### SSE 协议（前后端契约）

节点通过 `runtime.stream_writer` 写 dict，`session_graph.astream(stream_mode=["custom", "updates"])` 取出后由 `QueryService` 包成 `data: {json}\n\n`（单轮图的事件由 `run_pipeline` 转发；`updates` 里的 `__interrupt__` 转成 `clarify` 事件）。请求体 `{"query", "today"?, "thread_id"?, "resume"?}`，`thread_id` 只允许字母数字、`-`、`_`，最长 64，不传则服务端生成；`resume` 是对反问的回答（最长 200，必须同时带 `thread_id`）：

```json
{"type": "session", "thread_id": "..."}
{"type": "progress", "step": "校验SQL", "status": "running|success|error", "detail": "可选，失败原因"}
{"type": "rewrite", "standalone_question": "改写后的独立问题", "intent": "new|followup|result_qa"}
{"type": "clarify", "question": "反问的问题", "options": ["候选回答"]}
{"type": "answer", "text": "基于上一轮结果的直接回答"}
{"type": "result", "data": [{"列名": "值"}], "sql": "最终 SQL", "truncated": false, "max_rows": 1000}
{"type": "error", "message": "..."}
```

`session` 总是第一条；前端收到后保存 `thread_id`，之后的请求都带上它，“新会话”按钮清空它。收到 `clarify` 时前端记下待回答的消息，下一次发送（点选项或直接输入）作为 `resume`；点“跳过”则下一次发送作为新问题。一轮结束时只会有 `result`、`answer`、`clarify`、`error` 之一

`step` 的**中文名就是协议的一部分**（会话图的步骤是“理解问题”“反问澄清”“基于结果回答”）：`frontend/src/components/StepRail.tsx` 里的 `nodes` 数组按这个字符串匹配节点状态，坐标和连线（`connectors`）也是手写的。前端 `upsertStep` 按步骤名记录最新状态，并统计进入 `running` 的次数（循环中的步骤显示 `×N`）。前端类型定义在 `frontend/src/types/agent.ts`，解析在 `frontend/src/lib/agentApi.ts`。

### 标识与数据约定

- 表 id = 表名；字段 id = `表名.字段名`；指标 id = 指标名；取值 id = `表名.字段名.取值`
- 字段 `role`：`primary_key` / `foreign_key` / `dimension` / `measure`；表 `role`：`fact` / `dim`
- 教学数仓（`docker/mysql/dw.sql` 只建表，数据由 `app/scripts/dw_generator.py` 用固定种子生成）：4 张事实表 + 7 张维度表，覆盖 **2024-01-01 ～ 2025-12-31**（所以 `agent.today` 默认 `2025-12-31`）。设计说明见 `docs/design/dw-schema-v2.md`
  - 事实表：`fact_order`（订单粒度，约 5.9 万）、`fact_order_item`（订单行粒度，约 8.6 万）、`fact_refund`（退款申请粒度）、`fact_user_behavior`（浏览/收藏/加购，**没有“下单”**，下单看订单表）
  - 维度表：`dim_date`（`quarter` 是整数 1-4）、`dim_region`（城市级，`area` 是大区）、`dim_user`、`dim_category`（三级类目，经 `dim_sku.category_id` 关联，雪花）、`dim_shop`、`dim_sku`、`dim_activity`
  - 角色扮演维度：`dim_date` 被 `order_date_id` / `pay_date_id` / `apply_date_id` / `date_id` 引用；`dim_region` 被 `fact_order.receiver_region_id`（收货城市）和 `dim_user.region_id`（常住城市）引用
  - 已支付 = `fact_order.pay_time IS NOT NULL`；订单状态不随退款变化，退款只看 `fact_refund.refund_status = '退款成功'`
  - 金额：订单头 `order_amount = total_amount − discount_amount + freight_amount`；明细 `pay_amount = item_amount − discount_amount`（不含运费）；订单头与明细关联后对订单头金额求和会扇出
- 指标口径（`conf/meta_config.yaml` 的 13 个指标，description 里写明公式，会原样进入 SQL 生成 Prompt）：下单金额（GMV，含未支付）、支付金额（销售额的默认口径）、净支付金额、下单/支付订单数、支付买家数、客单价、笔单价（AOV）、件单价（ASP）、毛利、退款率、浏览转化率、复购率
- 生成器内部金额按“分”做整数运算，保证订单头与明细严格对平；`tests/test_dw_generator.py` 校验这些不变式和可复现性。**改生成器会改变数据，未来评测集的标准答案要随之重算**
- `docker/snapshot/dw_snapshot.sql.gz`：同一份数据的自包含 SQL 快照（建库 + 建表 + 数据，约 5 MB，解压 34 MB），由 `export_dw_sql` 从生成器直接渲染，字节级可复现；导入后与 `generate_dw_data` 写入的逐表 `CHECKSUM TABLE` 一致。`tests/test_export_dw_sql.py` 会检查它是否过期（约 5 秒）。导入方法和探查用 SQL 见 `docker/snapshot/README.md`

## 开发约定

- **每次改动都要同步更新 `CLAUDE.md` 和 `README.md`**：新增或改名的命令、配置项、目录、接口、已知问题，都在同一个提交里写进文档
- 每个 `.py` 文件以中文模块 docstring 开头说明职责，函数有一句话 docstring，行内注释解释“为什么”。新代码保持这个风格
- 节点统一写法：`async def node(state: DataAgentState, runtime: Runtime[DataAgentContext])`；开头发 `running`，成功发 `success`，`except` 里 `logger.error` + 发 `error` 后 `raise`；返回只包含本节点更新的 key 的 dict
- 日志统一 `from app.core.log import logger`（loguru，自动带 request_id），不要用 `print` 或标准库 `logging`
- Prompt 放 `prompts/<name>.prompt`，用 `load_prompt("<name>")` 读取、`PromptTemplate` 渲染。模板里的字面量花括号要写成 `{{ }}`（见 `filter_table_info.prompt`）
- `meta_config.yaml`：表描述写明粒度（一行代表什么）；外键必须写 `ref: 表名.字段名`；只对取值有限的文本字段开 `sync: true`；`tests/test_meta_config.py` 会检查它和 `dw.sql` 的表字段完全一致
- 业务术语写在 `meta_config.yaml` 的 `terms`：定义里涉及的字段必须写成 `表名.字段名`（构建时校验存在，问数时补进候选）；只放必须靠业务约定才能确定的口径
- 已验证问答示例写在 `conf/sql_examples.yaml`：每条都要在数仓上执行过；覆盖“写法模式”，年份、实体、问法要与评测题不同（`evals/dataset.py` 会做相似度检查）
- 需要结构化输出的节点用 `JsonOutputParser`，Prompt 里明确“仅输出 JSON”；让 LLM 只做选择，结构裁剪由程序完成
- 给 LLM 的结构化上下文一律 `yaml.dump(..., allow_unicode=True, sort_keys=False)`
- 任何要在数仓上执行的 SQL 都必须经过 `app/core/sql_safety.py` 的 `ensure_readonly`（`DWMySQLRepository.validate/run` 已内置）；LLM 产出的 SQL 先过 `clean_sql`
- 检索阈值、集合/索引名、SQL 闭环参数等可调项放进 `conf/app_config.yaml`，不要在代码里写死
- 改了 `app/core/` 下的纯函数要补 `tests/` 里的 pytest 用例
- 配置项要同时改 `conf/app_config.yaml` 和 `app/conf/app_config.py` 的 dataclass（OmegaConf 结构化校验，缺字段或多字段都会在导入时报错）；敏感值走 `.env` + `${oc.env:VAR}`
- 前端：函数组件 + hooks，无状态管理库和路由；样式只用 Tailwind 工具类，主题色（`parchment` / `ink` / `moss` / `brass` / `tomato`）定义在 `tailwind.config.ts`

## 常见改动要动哪些文件

- **新增图节点**：`app/agent/nodes/<node>.py` → `graph.py` 注册节点和边 → 需要的新状态加到 `state.py` → 新外部依赖加到 `context.py`、`api/dependencies.py`、`services/query_service.py`，以及 `graph.py` 末尾的本地调试代码 → `StepRail.tsx` 的 `nodes` / `connectors` 加上同名步骤（否则前端不显示该步骤）。要在 `run_sql` 之后接节点（总结、图表等），直接读 `state["result"]`
- **新增 SSE 事件类型**：节点里 `writer({...})` → `frontend/src/types/agent.ts` 的 `AgentEvent` → `App.tsx` 的 `onEvent`（未识别的事件类型会被忽略，只有 `type: error` 才显示为失败）
- **会话图加节点 / 分支**：`app/agent/nodes/<node>.py`（参数类型是 `SessionState`）→ `session_graph.py` 的 `build_session_graph` → 新的本轮字段加到 `SessionState` 并保证每轮覆盖 → `StepRail.tsx`。意图取值改动同步 `app/core/conversation.py` 的 `INTENTS`、`prompts/contextualize_question.prompt`、`frontend/src/types/agent.ts` 的 `Intent` 和 `MessageBubble.tsx` 的 `intentLabels`
- **新增多轮评测题**：加到 `evals/datasets/multiturn.yaml`（第 2 轮起必须有 `gold_intent`、`gold_standalone`，`trap_sql` 一般是“没有正确承接上文”的写法）→ `uv run python -m evals.check_dataset -d evals/datasets/multiturn.yaml --per-category 0` → `uv run pytest`（`tests/test_eval_dataset.py` 固定了 20 段对话）。改写 Prompt 的 few-shot 示例与评测题不要用相同的实体和年份（示例用 2024 年）
- **数仓加表/字段/指标**：改 `docker/mysql/dw.sql` → 改 `app/scripts/dw_generator.py` 生成对应数据 → 改 `conf/meta_config.yaml`（外键加 `ref`）→ 跑 `generate_dw_data`（会按 dw.sql 重建表）→ 重跑知识库构建（删除了表或指标时加 `--reset`）→ `export_dw_sql` 重新导出快照（同步更新 `docker/snapshot/README.md` 的关系表）→ `uv run pytest`
- **新增评测题**：加到 `evals/datasets/traps.yaml`（字段说明见文件头）→ 每题必须有 `gold_sql` 和一条典型错误写法 `trap_sql` → `uv run python -m evals.check_dataset` 确认错误写法的结果与标准答案不同、保留两位小数后仍可区分 → `uv run pytest`（每类题数在 `tests/test_eval_dataset.py` 里固定为 5，增加题数时同步修改）
- **新增业务术语 / 问答示例**：改 `meta_config.yaml` 的 `terms` 或 `conf/sql_examples.yaml` → `uv run python -m evals.check_dataset`（示例能执行、不泄漏）→ 重跑知识库构建
- **新增语义检查规则**：`app/core/sql_lint.py` 加纯函数规则 → `tests/test_sql_lint.py` 必须保持“评测集全部标准 SQL 零误报”
- **新增配置项**：`conf/app_config.yaml` + `app/conf/app_config.py` 的 dataclass 两处同时改
- **Meta 库加表/加列**：`docker/mysql/meta.sql` + `app/models/` + `app/entities/` + mapper + `MetaMySQLRepository`。没有迁移工具，`meta.sql` 只在 MySQL 卷首次初始化时执行，已有数据卷需要 `docker compose -f docker/docker-compose.yaml down -v` 重建或手动 `ALTER TABLE`（例如 `ref_column` 列：`ALTER TABLE meta.column_info ADD COLUMN ref_column VARCHAR(128)`）
- **新增接口**：`app/api/routers/` 新建 router，在 `main.py` `include_router`
- **notebook**：放 `notebooks/`，依赖加到 `pyproject.toml` 的 `notebook` 组（不要加进 `dev`）；提交前 `uv run ruff check notebooks` 和 `uv run ruff format notebooks`（ruff 会检查 `.ipynb`），草稿类 notebook 清空输出再提交

## 评测集（`evals/`）

- `evals/datasets/traps.yaml`：10 类业务口径陷阱 × 5 题（下单与支付口径、订单头/明细扇出、退款扣减、日期角色、地区角色、雪花关联、去重计数、活动归因、相对时间、漏斗分母），“今天”统一是 2025-12-31。每题有标准 SQL `gold_sql`、典型错误写法 `trap_sql` 及其说明、用于归因的 `expect`（表 / 指标 / 取值）
- 题目口径以 `meta_config.yaml` 的指标定义为准：“销售额”= 支付金额（`pay_time IS NOT NULL`，按 `pay_date_id` 筛时间）；按商品/类目/品牌/活动拆分金额用明细 `pay_amount`；退款只认“退款成功”
- `evals/compare.py`：结果等价判定（忽略列名和列顺序、允许多出列、数值相对误差 1e-9、接受预测值四舍五入到 1~4 位小数、比率列允许 ×100、只有 `order_matters` 才比较行顺序；时间标签换算：整列是同一年的“年月”标签（`'2025-01'`、`202501`、`'2025年1月'`）时可按月份数字与标准列比较，日期 `'2025-12-25'` 与日期键 `20251225` 互认），`tests/test_compare.py` 覆盖每条规则。放宽判分规则后必须重跑 `check_dataset` 和 `--predictor trap`，确认没有把错误写法判对
- `evals/dataset.py`：不连库的静态检查（pytest 会跑）；`evals/check_dataset.py`：连数仓执行标准 SQL 和错误写法
- 评测题依赖生成数据：**改了 `dw_generator.py` 或数据规模后必须重跑 `check_dataset`**，个别题的区分度可能因此消失（例如 `date_role_01` 只差 1 单）
- `evals/runner.py`：答题打分。`--predictor agent` 用问数图答题，并通过 `graph.astream(stream_mode=["updates", "custom"])` 捕获每个节点的输出做归因（召回遗漏 → 过滤误删 → SQL 不可执行 → 结果不符，`evals/scoring.py`）；`gold` / `trap` 用标准 SQL / 错误写法作答，应分别为 100% / 0%，用来自检打分逻辑。支持 `--paraphrases`、`--no-examples`、`--no-semantic-check`、`--llm-cache`（`evals/llm_cache.py`，sqlite 录制回放，录制好的 `evals/.cache/glm-5.3-flash.sqlite` 随仓库提交，可直接回放，其余缓存文件仍忽略）、`--baseline` / `--save-baseline`（退化时退出码 1）。报告在 `evals/reports/`（已 gitignore）
- `evals/datasets/multiturn.yaml`：22 段对话（省略、追加条件、换维度 / 下钻、引用上一轮结果、纠正口径、新话题、需要反问），48 轮；第 2 轮起标注 `gold_intent` / `gold_standalone`。`result_qa` 轮另有 `gold_answer_contains`（直接回答时回答里必须出现的内容，`check_dataset` 会确认它们出现在标准结果里），Agent 退回查库时仍按 `gold_sql` 判分；`clarify` 轮另有 `clarify_reply`（扮演用户回答反问），`gold_standalone` / `gold_sql` 是澄清之后的问题和答案
- `evals/multiturn.py`（`--multiturn teacher|free`）：用会话图逐轮作答，`subgraphs=True` 捕获单轮图节点输出做归因，意图判断错误归为“意图错误”（意图按第一次判断计，衡量该不该反问）。会话停在反问处时用 `clarify_reply` 恢复（不该问却问了时用默认回答“按你的理解直接查询”）。`teacher` 每轮新开会话、用标准答案（含标准 SQL 执行结果的预览，以及上一轮完整结果 `last_result`）填充历史；`free` 用同一个 thread_id。汇总 `summarize_dialogs`：意图准确率、追问轮准确率、整段对话成功率，`accuracy` / `per_case`（`id#轮次`）与单轮同名，可复用基线比较
- 单轮 runner 仍直接调用单轮图（不经过会话图），单轮基线不受多轮改造影响；单轮模式会跳过多轮题
- `notebooks/eval_tutorial.ipynb`：评测教程（思路、判分规则演示、在库自检、单题追踪、归因、真实结果解读、Windows 差异附录）。第 9～10 节读取 `evals/results/glm-5.3-flash/`
- `notebooks/eval_code_walkthrough.ipynb`：评测代码精读（逐文件讲设计取舍、用小实验找判分器漏洞、带着问题重读真实结果、问题清单与练习）。用 `ast` 从源文件取代码展示，不导入 `app.conf.app_config`、不需要 LLM key；第 4 节和 10.5 节连数仓，连不上时跳过。改了 `evals/` 的函数名要同步检查它
- `evals/results/<模型>/<实验名>/`：提交进仓库的真实评测结果（从 `evals/reports/<时间戳>/` 复制的 `summary.json`、`results.jsonl`、`summary.md`），实验名为 `baseline`（含换问法）、`no_examples`、`no_lint`、`mt_teacher`、`mt_free`，以及多轮第一阶段代码的 `mt_teacher_phase1`、`mt_free_phase1`（对照用）；`evals/baselines/<模型>.json` 是回归门禁用的基线。当前结果：单轮 91%（原问法 96%），多轮自由对话追问轮 96%；剩余失败里 JSON 解析（模型输出全角引号）约占一半

## 已知问题与陷阱

改动相关代码前先了解这些，它们是现状而不是设计目标：

**运行环境（暂不处理）**
- 没有 `LLM_API_KEY` 时，任何 `import app.conf.app_config` 的模块（几乎全部）都会在导入阶段报 OmegaConf 插值错误。只做静态检查时可临时 `LLM_API_KEY=dummy uv run ...`；`tests/` 下的测试刻意不导入配置
- 所有服务地址、MySQL 账号密码都硬编码在 `conf/app_config.yaml` 和 compose 文件里；MySQL 地址是 `127.0.0.1`（避免 `localhost` 解析到 IPv6 或走 socket），其余服务是 `localhost`；MySQL 账号 `early`（密码 `dili123`）对 `dw` 和 `meta` 有全部权限，只读完全靠程序侧的 `ensure_readonly`
- 改了 MySQL 账号（compose 的 `MYSQL_USER`、`dw.sql` / `meta.sql` 的 GRANT、`app_config.yaml` 三处要一致）后，已有数据卷要 `docker compose -f docker/docker-compose.yaml down -v` 重建，初始化脚本只在卷第一次创建时执行

**SQL 闭环**
- `ensure_readonly` 基于 sqlglot 的 MySQL 方言解析，sqlglot 解析不了的合法 MySQL 语法会被当作“无法解析”拒绝，然后进入修正流程
- 行数上限用 `fetchmany` 实现，asyncmy 默认缓冲整个结果集，超大结果仍会先完整读进内存；靠 `max_execution_ms` 兜底

**检索**
- ES `min_score` 是 BM25 原始得分，2.0 是在教学数仓上实测标定的（正确命中最低约 3.8），数据量或取值分布变化后需要重新标定；共享词造成的噪声命中（“金卡会员”命中“普通会员”、“2025年”命中各活动名）得分也很高，阈值挡不住，只能靠过滤节点；Qdrant 余弦阈值 0.6 没有标定过（本地测试拿不到真实 bge 模型）
- 提示词必须逐字节确定，评测才可复现、LLM 缓存才能回放：`extract_keywords` 和三个 `recall_*` 节点用 `dict.fromkeys` 保序去重（原来用 `set`，每次启动顺序都可能不同，换进程后缓存只能命中检索词扩展那 3 次调用）。新增节点时不要用 `set` 决定任何进入提示词的顺序

**评测代码（精读发现，尚未修复，复现见 `eval_code_walkthrough.ipynb` 第 11 节）**
- `compare_with_baseline` 把“本次有必过题失败”当作退化，基线里 `activity_01` 本来就失败，所以与当前基线比较时永远判退化（退出码 1）；它还直接比较两次评测的整体准确率，不检查题集是否一致（只跑原问法时与含换问法的基线比较有约 5 个点的虚假余量）
- 评测重新执行模型 SQL 时没有固定“当前日期”：SQL 里的 `CURDATE()` / `NOW()` 按跑评测当天计算，同一份缓存在不同日期回放可能得到不同分数（`relative_time_02` / `_04` 换问法把日期固定为 2025-12-31 后是对的）
- `compare.py`：×100 倍数规则对所有列生效（计数、金额也算）；多重集合比较用贪心匹配，带容差时判定结果可能随行顺序变化
- 多轮：直接回答只做子串匹配；free 模式下前一轮失败后改为查库，意图会被记为错误

**其他**
- `filter_table` / `filter_metric` / `recall_*` 用 `JsonOutputParser`，模型输出格式不对会直接抛异常中断整条流程，没有重试或降级
- 语义检查只看同一层 SELECT 里直接出现的物理表，派生表 / CTE 里的扇出不会被发现；它是提示而非硬约束，修正次数用完仍会执行
- 已验证问答示例会显著影响评测分数：衡量模型本身的能力时用 `--no-examples` 做对照；示例与评测题的相似度检查是文本层面的，不能完全排除“写法模式”层面的提示
- 关联路径补全只在候选表“不连通”时才加表；若召回已经带进了经由维度表的关联（如同时召回了 `dim_shop` 和 `dim_sku`），是否扇出取决于模型按 `ref` 写 JOIN 的质量
- 生成数据的用户只有 3000 个，人均订单多，浏览转化率、复购率明显高于真实业务，只适合检验 SQL 写法，不代表真实水平
- 请求级 `AsyncSession` 不支持并发使用。目前并行的 `recall_*` 节点不碰 MySQL 所以没问题；新增会访问 MySQL 的并行节点时要注意
**多轮（步骤 1～5 已完成，见 `borrowing-and-roadmap.md` 4.9、4.10）**
- 会话存在进程内的 `InMemorySaver`：服务重启即丢失（包括停在反问处、尚未回答的会话）；每轮会写多个包含完整历史的检查点，会话越长、越多，内存占用越大，没有过期清理。持久化（第 6 步）暂缓：要新增检查点存储（SQLite 文件或独立的 MySQL 库），需要先确认
- 直接回答里的计算（差值、占比）由 LLM 完成，没有程序校验，可能算错；评测只检查回答里有没有关键内容
- 首轮默认不经过改写节点，所以首轮问题不会反问（`conversation.contextualize_first_turn` 可打开，代价是每个首轮多一次 LLM 调用）
- 同一个 `thread_id` 的并发请求没有加锁（前端在流式期间禁用发送）；两个请求交错会让历史错乱
- 前端消息列表仍只在浏览器内存里，刷新后后端会话还在但页面看不到历史；没有会话列表接口
- 改写只看最近 3 轮且结果只有前 5 行：引用“第 8 名”这类预览之外的行会改写失败
- 改写失败（LLM 输出不是 JSON）时按原话当新问题处理，追问会被当作独立问题查询，可能查出与预期不同的结果；前端“理解问题”步骤会显示失败原因
- 权限控制、多租户、查询缓存、限流、监控告警等生产化能力尚未涉及（README“当前限制与规划”一节）

## 本地完整联调的注意事项

- Docker Hub 偶尔限流，可改用 `mirror.gcr.io/library/<image>` 拉取后 `docker tag` 成 compose 里的名字
- ES 节点磁盘使用率超过 90% 时会拒绝分配分片（集群 red，构建报 503）；本地开发可执行
  `curl -XPUT localhost:9200/_cluster/settings -H 'content-type: application/json' -d '{"persistent":{"cluster.routing.allocation.disk.threshold_enabled":false}}'`
- ES 的 IK 插件 zip 必须和 ES 版本完全一致（当前 8.19.10）
- TEI：`bge-large-zh-v1.5` 只有 `pytorch_model.bin`、没有 ONNX，启动日志里的 `Could not start ORT backend` 是正常的，会退回 candle 后端（一批最多 4 条）。`MAX_CONCURRENT_REQUESTS` 不能设太小：原来的 16 会在知识库构建一次发 20 条时让 TEI 队列溢出 panic 并重启（构建报 `Server disconnected`），现为 512。单次请求最多 32 条（TEI 默认），超过返回 413
- 模型下载：`hf download` 的大文件走 `us.aws.cdn.hf.co`，网络有白名单时要一并放行
