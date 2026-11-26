# 开源 Data Agent 借鉴评估与路线图

围绕三个落脚点：**① SQL 生成逻辑　② Data Agent 评测　③ 多轮问数**。
本文对照当前代码（教学数仓 v2，见 `dw-schema-v2.md`）评估各开源项目和业界实践中哪些做法值得借鉴，记录本次已落地的部分，并给出多轮问数的实施方案。

---

## 一、对研读文档的校正

《Data Agent 开源项目研读与学习计划》对各项目亮点的概括基本准确。对照当前仓库，有几处需要更新或修正：

| 文档中的说法 | 当前仓库的实际情况 | 结论 |
| --- | --- | --- |
| 示例基于 `fact_order.region_id`、`dim_product`、`GMV = SUM(order_amount)` | 数仓已换成 v2：收货城市是 `receiver_region_id`，类目是雪花结构，“销售额”默认是支付金额（需 `pay_time IS NOT NULL`） | 示例代码不能直接照搬，口径以 `conf/meta_config.yaml` 为准 |
| “补 BFS 找 JOIN 路径即可” | 已实现（`app/agent/join_path.py`），而且**不能用无权 BFS**：订单表到类目表，“订单 → 店铺 → 商品 → 类目”和“订单 → 明细 → 商品 → 类目”一样长，无权 BFS 会选中前者，把订单关联到店铺的全部商品造成扇出。现实现为带权最短路，从维度表往下展开的代价更高 | 已完成，比文档方案更稳妥 |
| `result_match` 用 `round(v, 4)` + `Counter` 比较结果 | 会把“比率 vs 百分数”“四舍五入到两位小数”判错，多出列时只是简单处理。`evals/compare.py` 已覆盖这些情况，并用评测题验证过 | 用现有判定器 |
| L2 口径检查按指标写断言（如 GMV 必须含 `SUM(order_amount)`） | 新口径下，同一个 `SUM(order_amount)` 既可能是下单金额也可能是支付金额，按指标写断言容易误判，维护成本也高 | 改为**通用规则**：按外键 `ref` 检查扇出和关联条件（不依赖具体指标），口径对错交给评测集的结果比对 |
| Uber 的 Intent Agent 先路由业务域 | 当前只有 11 张表、一个交易域，路由收益很小 | 表数量上到几十张、出现多个业务域时再做 |
| WrenAI 语义层编译 | 收益明确，但需要为指标、维度、关系建一套编译器，覆盖不了的问题还要回退 | 先做“半结构化”版本：指标描述写明公式 + 语义检查 + 示例库；完整编译列入后续路线 |
| BIRD-Interact 的成功率数字 | 不同版本、榜单之间的数字不一致 | 引用时以论文最新版和官方榜单为准 |

---

## 二、本次已落地的借鉴（落脚点 ① SQL 生成）

| 改动 | 借鉴来源 | 解决的问题 | 如何验证 |
| --- | --- | --- | --- |
| **SQL 语义检查** `app/core/sql_lint.py`，接在 `validate_sql` 的 EXPLAIN 之后 | WrenAI 的 relationships（关联只能按声明的关系走）；Uber QueryGPT 的分环节校验 | EXPLAIN 只能保证能执行。新增两条规则：① 聚合父表字段时又关联了引用它的子表 → 扇出；② 关联条件不符合任何 `ref` → 连错字段。问题交给 `correct_sql` 修正；修正次数用完时只告警、不阻断 | 评测集 50 条标准 SQL 零误报；扇出类 4 条错误写法全部检出（`tests/test_sql_lint.py`）。端到端：扇出写法被拦下，修正后结果正确 |
| **业务术语** `meta_config.yaml` 的 `terms` | SQLBot 的术语配置 | 消除业务说法的歧义：“双11当天 / 双11期间 / 双11活动商品”分别对应日期、活动起止时间、活动归因；“城市”默认指收货城市；“高价值会员”= 铂金 + 钻石等 | 精确匹配名称和别名；术语引用的字段会补进候选。端到端：“高价值会员”命中后，`dim_user` 被带进候选表 |
| **已验证问答示例** `conf/sql_examples.yaml` | Vanna 的 question-SQL 训练、Databricks Genie 的 example SQL、Cortex Analyst 的 verified queries | 复杂写法（用 EXISTS 避免扇出、净额要先按订单汇总退款、转化率的分子分母口径）靠示例教给模型，比在 Prompt 里堆规则更有效；示例 SQL 用到的表也是选表的强信号 | 10 条示例都在数仓上执行过；**防泄漏**：与评测题的问题相似度 ≥ 0.8、或 SQL 相似度 ≥ 0.9 即报错，初版中有 2 条示例因此被删除 |
| `today` 改为请求级 | 评测和多轮的前置条件 | 每道题、每个会话都可以指定自己的“今天” | 接口 `today` 字段，非法日期在入口处被拒绝 |

**设计取舍**：这几项都是**确定性**的增强，效果不依赖模型能力，可以离线验证。模型相关的改进（示例到底能提升多少准确率）需要用真实 LLM 跑 runner 的消融实验来量化，见下一节。

---

## 三、评测 runner（落脚点 ②）

### 用法

```bash
uv run python -m evals.runner                                  # 问数 Agent 答全部 50 题
uv run python -m evals.runner --paraphrases                    # 加上每题的其他问法（借鉴 Genie）
uv run python -m evals.runner --predictor gold                 # 自检：用标准 SQL 作答，应为 100%
uv run python -m evals.runner --predictor trap                 # 自检：用错误写法作答，应为 0%
uv run python -m evals.runner --llm-cache evals/.cache/llm.sqlite   # 录制 / 回放 LLM 调用
uv run python -m evals.runner --baseline evals/baseline.json --save-baseline   # 保存基线
uv run python -m evals.runner --baseline evals/baseline.json   # 与基线比较，退化时退出码为 1
```

### 每道题记录什么

- **结果**：执行模型的最终 SQL 和标准 SQL，用 `evals/compare.py` 比较结果集（借鉴 Genie）
- **归因**（借鉴 Uber）：捕获问数图每个节点的输出，按“流程异常 → 召回遗漏 → 过滤误删 → SQL 不可执行 → 结果不符”找出第一个出问题的环节。召回和过滤是否遗漏，依据每题 `expect` 中的表、指标和取值判断
- **成本**：LLM 调用次数、token 数、耗时、SQL 修正次数，以及最终 SQL 是否仍有语义检查提示

### 推荐的第一组实验（需要真实 LLM）

| 实验 | 命令 | 回答的问题 |
| --- | --- | --- |
| 基线 | `--paraphrases --llm-cache ... --save-baseline` | 当前整体准确率、各类陷阱准确率、失败主要出在哪个环节 |
| 去掉示例 | `--no-examples` | 已验证问答示例带来多少提升 |
| 去掉语义检查 | `--no-semantic-check` | 扇出检查挽回了多少题，代价是多少次额外 LLM 调用 |
| 问法鲁棒性 | 对比 `question` 与 `paraphrase_1` 的准确率 | 换个说法还能不能答对 |

面试时最有说服力的表述是：“X% 的错误出在召回环节，所以我做了 Y，这一类的准确率从 a 提升到 b”。runner 的报告直接给出这些数据。

### 已验证的部分（没有 LLM API key 的环境）

- `gold` 模式 100%（50/50），`trap` 模式 0%，50 题全部归因为“结果不符”
- 基线比较：以 gold 为基线跑 trap，判定为退化，列出全部必过题，退出码为 1
- `agent` 模式：用按题目应答的假 LLM 跑真实问数图，节点输出捕获、修正次数（扇出题修正 1 次）、LLM 调用计数（6 / 7 / 8 次）和归因（不可执行的 SQL 归为“SQL 不可执行”）均符合预期
- **尚未验证**：真实模型的准确率

---

## 四、多轮问数方案（落脚点 ③）

### 4.1 借鉴来源

| 来源 | 借鉴什么 |
| --- | --- |
| Snowflake Cortex Analyst（[多轮博客](https://www.snowflake.com/en/engineering-blog/cortex-analyst-multi-turn-conversations-support)） | 在入口加一个改写节点，把追问改写成**独立完整的问题**，后续流水线不动。他们也试过把历史直接传给每个 LLM 节点，效果不好。few-shot 示例要覆盖“与上文相关就改写”和“与上文无关就原样保留”两种情况 |
| Cortex 的已知限制 | 拿不到上一轮的查询结果，所以“第一名再拆一下”这类追问处理不了 → **我们在会话记忆里保存结果摘要，补上这块** |
| Spring AI Alibaba DataAgent | 人在流程中途介入：在节点前中断，用 threadId 找回状态继续执行。LangGraph 对应 `interrupt()` + `Command(resume=...)` |
| BIRD-Interact、CoSQL | 多轮评测：每轮都有标准 SQL，同时看单轮准确率和整段对话成功率；可以注入歧义来考察澄清能力 |

### 4.2 架构：外层会话图 + 内层单轮流水线

```
            ┌──────────── 会话状态（checkpointer，按 thread_id 持久化） ────────────┐
            │ history: 每轮的原问题 / 改写后的问题 / 最终 SQL / 结果摘要 / today       │
            └──────────────────────────────────────────────────────────────────────┘
用户本轮输入
   │
   ▼
[contextualize] 改写 + 判断意图（一次 LLM 调用；首轮跳过）
   ├─ new / followup ──▶ [run_pipeline] 调用现有单轮问数图（不改动）──▶ [remember] 追加本轮记录
   ├─ result_qa ──────▶ [answer_from_result] 基于上一轮结果摘要直接回答，不查库
   └─ clarify ────────▶ [ask_clarification] interrupt() 反问用户 ──(Command(resume))──▶ contextualize
```

**关键决定：checkpointer 只挂在外层会话图上，内层单轮流水线照常以普通调用方式运行。** 理由：

1. 单轮 State 里有大量中间结果（召回到的字段、表结构上下文等），每个节点执行后都写一次检查点，体积大、序列化成本高，跨轮也没有保留价值
2. 内层图的 `sql`、`correct_attempts`、`lint_issues` 等字段是“本轮”的数据，放进持久化状态反而要小心清理，否则上一轮的值会串到下一轮
3. 需要中断的地方（澄清）放在外层，进入流水线之前就决定，内层不需要中断能力

### 4.3 会话状态

以下是已实现的版本（`app/agent/state.py`），与最初设想的差别见 4.9：

```python
class ResultPreview(TypedDict):
    columns: list[str]
    row_count: int
    truncated: bool
    rows: list[dict]       # 前 5 行，值已转成基础类型

class Turn(TypedDict):
    question: str          # 用户原话
    standalone: str        # 改写后的独立问题
    intent: str            # new / followup / result_qa（clarify 留到第 5 步）
    sql: str | None
    result_preview: ResultPreview | None   # 给下一轮引用
    error: str | None      # 失败的轮次也记录，下一轮仍能承接它的问题
    today: str

class SessionState(TypedDict):
    user_input: str
    today: NotRequired[str]
    history: Annotated[list[Turn], operator.add]   # reducer：每轮只追加
    # 本轮数据，每轮完整覆盖
    intent: str
    standalone: str
    sql: str | None
    result_preview: ResultPreview | None
    error: str | None
```

### 4.4 Checkpointer 选型

- 开发：`langgraph.checkpoint.memory.InMemorySaver`（已随 langgraph 安装）
- 持久化：社区包 `langgraph-checkpoint-mysql[asyncmy]` 提供 `AsyncMySaver`，使用与项目相同的 asyncmy 驱动，要求 MySQL ≥ 8.0.19（项目用的是 8.0 最新版），`.setup()` 建表时需要开启 autocommit（[PyPI](https://pypi.org/project/langgraph-checkpoint-mysql/)）。建议使用单独的 `agent_memory` 库，不和 `meta` 混在一起

### 4.5 新增节点

| 节点 | 做什么 | 要点 |
| --- | --- | --- |
| `contextualize` | 输出 `{intent, standalone_question, clarify_question}` | Prompt 输入最近 3 轮的 `standalone + sql + result_summary`；用 few-shot 覆盖：省略（“那华东呢”）、追加条件（“只看金卡会员”）、换维度（“按月拆开”）、引用结果（“第一名的再拆”）、纠正口径（“我要的是销量”）、新话题（与上文无关，原样保留）。首轮直接跳过 |
| `run_pipeline` | 以 `standalone_question` 和 `today` 调用现有图 | `followup` 时把上一轮 SQL 作为“参考 SQL”传进 `generate_sql`：增量修改比从头生成更稳定，也能保持口径一致 |
| `answer_from_result` | 上一轮结果不超过 200 行时直接回答“哪个最高”“占比多少” | 结果太大时退回 `run_pipeline` |
| `ask_clarification` | `interrupt({"question": ..., "options": [...]})` | 只在三种情况触发（借鉴研读文档对 DataAgent 的总结）：口径未定义（“最近”是 7 天还是本月）、候选冲突（“地区”在收货地和常住地之间无法确定）、预估扫描量过大。其余情况直接执行 |
| `remember` | 追加本轮 `Turn` | 结果摘要只保留列名、行数和前 5 行，控制 token |

### 4.6 接口与前端

- `POST /api/query` 增加 `thread_id`（不传时由服务端生成，并在第一条 SSE 事件中返回）和 `resume`（回答澄清问题时使用）
- 新增 SSE 事件：`{"type": "session", "thread_id"}`、`{"type": "rewrite", "standalone_question", "intent"}`、`{"type": "clarify", "question", "options"}`、`{"type": "answer", "text"}`。`App.tsx` 目前把未知事件都当作错误处理，要一并修改
- 前端展示“我理解为：……”，用户可以直接纠正；会话列表从 `GET /api/sessions` 读取（数据来自 checkpointer 的 `alist`，或另建一张轻量的会话表）

### 4.7 多轮评测（runner 扩展）

- 评测集格式已预留 `turns`（见 `eval-and-multiturn.md` 3.8），每轮标注 `gold_standalone`、`gold_intent`、`gold_sql`
- runner 对多轮题使用同一个 `thread_id` 依次作答，提供两种模式：
  - **teacher forcing**：上一轮的历史用标准答案填充，避免前一轮答错连累后面所有轮次，用来测“单轮追问能力”
  - **自由对话**：用模型自己的历史，计算整段对话成功率（任何一轮答错整段即失败），反映真实体验
- 指标：意图准确率、改写准确率（把改写后的问题单独走单轮流程，与 `gold_sql` 比较结果，而不是逐字比对改写文本）、轮次准确率、整段对话成功率
- 题目设计参考 BIRD-Interact：在部分题里注入歧义，期望系统发起澄清，而不是直接猜

### 4.8 实施步骤

| 步骤 | 产出 |
| --- | --- |
| 1 | `SessionState` + 外层会话图（`InMemorySaver`）+ `contextualize` + `remember`；单轮流水线以普通调用方式接入 |
| 2 | 接口支持 `thread_id`，新增 `session` / `rewrite` 事件，前端展示改写结果 |
| 3 | 多轮评测题 20 段左右 + runner 多轮模式（teacher forcing / 自由对话） |
| 4 | `followup` 时把上一轮 SQL 作为参考；`answer_from_result` 分支 |
| 5 | `ask_clarification`（interrupt / resume）+ 前端澄清卡片 |
| 6 | 换成 `AsyncMySaver` 持久化；会话列表与历史加载 |

每一步都先跑单轮评测，确认单轮准确率没有下降。

### 4.9 实施进度（步骤 1～3 已完成）

**落地内容**

| 步骤 | 文件 |
| --- | --- |
| 1 | `app/agent/session_graph.py`（`contextualize → run_pipeline → remember`，`InMemorySaver`）；节点 `app/agent/nodes/{contextualize,run_pipeline,remember}.py`；纯函数 `app/core/conversation.py`；Prompt `prompts/contextualize_question.prompt`（7 个 few-shot：省略、追加条件、换维度、引用结果、纠正口径、结果追问、新话题，统一用 2024 年，避开评测题）；配置 `conversation.history_turns` / `result_preview_rows` |
| 2 | 接口 `thread_id`；SSE `session` / `rewrite` 事件；前端保存会话 id、“新会话”重置、回答顶部“我理解为：……”与“理解有误”（把改写放回输入框修改）、流程图新增“理解问题”节点；未知事件类型不再当作错误 |
| 3 | `evals/datasets/multiturn.yaml`（20 段、44 轮，6 类追问）；`evals/multiturn.py` + `runner --multiturn teacher|free`；`summarize_dialogs` 等打分函数 |

**与 4.2～4.7 设想的差别**

- 意图暂时只有 `new` / `followup` / `result_qa`。`clarify` 要配合 interrupt，留到第 5 步；在那之前，模型遇到歧义时按业务术语的默认口径改写（与单轮行为一致）
- `result_qa` 暂时也改写成可以独立查询的问题，重新查库（第 4 步再加 `answer_from_result`）；评测里这类轮次因此也用 `gold_sql` 判分
- 结果摘要存成结构化的 `result_preview`（而不是一段文本），进 Prompt 时统一转 YAML；失败的轮次也写进历史（带 `error`），用户接着说“换成按月看”时仍能承接
- 单轮图必须用 `compile(checkpointer=False)`：实测默认编译的图在节点里被调用时会继承外层 checkpointer，写出 `run_pipeline:<id>` 命名空间的检查点，正是 4.2 想避免的情况
- 改写失败（输出不是 JSON）时不中断，按原话当新问题处理
- 多轮评测的“改写准确率”没有单独跑一遍：现阶段单轮流水线只接收改写后的问题，“改写后单独走单轮流程”与本轮作答完全相同，轮次准确率就是改写准确率。第 4 步把上一轮 SQL 作为参考传进 `generate_sql` 后，两者才会分开，届时再加
- 归因新增“意图错误”环节：意图判错且答错的轮次优先归到这里；报告的失败明细并列“预测改写 / 标准改写”，便于判断是改写错还是 SQL 错

**已验证（没有 LLM API key、拿不到 bge 模型的环境）**

用固定种子数仓 + 真实 Qdrant / ES / Meta MySQL，Embedding 用哈希向量代替，LLM 用“按评测集题目应答”的假模型（改写按 `gold_standalone`，生成按 `gold_sql`）：

- 数据集：`check_dataset` 44 轮全部通过（标准 SQL 有结果，每轮错误写法都与标准答案不同）；单轮评测集仍全部通过
- 打分自检：`--multiturn teacher --predictor gold` 100%，`--predictor trap` 0%；单轮 `--predictor gold` 仍为 100%
- `agent` 模式：teacher / free 两种模式都跑通 20 段对话，首轮 6 次 LLM 调用、追问轮 7 次；故意把一轮意图判错后，该轮归为“意图错误”，报告列出意图混淆和改写对比
- HTTP：同一 `thread_id` 连续三轮（列表 → “排第一的是哪个大区？” → “它每个月的销售额呢？”），事件顺序为 `session → progress… → rewrite → … → result`；非法 `thread_id` 返回 422
- 检查点：只有根命名空间，单轮中间状态没有进检查点；失败轮次带 `error` 写进历史，下一轮正常承接；客户端中途断开后本轮不入历史，下一次请求从头开始
- 前端：浏览器里连续追问，“理解问题”节点、改写横幅、“理解有误”回填、“新会话”清空会话 id 均正常
- 单轮 runner 的 agent 模式行为不变（仍直接调用单轮图，每题 6 次 LLM 调用）

**真实模型验证**：后来用 glm-5.3-flash 跑了完整评测，第一阶段的结果见 `evals/results/glm-5.3-flash/mt_*_phase1/`，与第 4、5 步之后的对比见 4.10。

### 4.10 实施进度（步骤 4、5 已完成，步骤 6 暂缓）

**步骤 4：追问带参考 SQL、基于上一轮结果回答**

- `run_pipeline` 在 `followup` 时把最近一条有 SQL 的轮次的问题和 SQL 作为 `reference` 传给 `generate_sql`。`{reference}` 在模板里紧挨在示例和任务要求之间，为空串时提示词与原来逐字节相同：用单轮评测的 LLM 缓存在改动后重跑 8 次作答，52 次调用全部命中，单轮基线不受影响
- 新节点 `answer_from_result`：`result_qa` 且上一轮结果不超过 `conversation.answer_max_rows`（200）行、没有截断时，把完整结果交给 LLM 直接回答（`answer` 事件），不再查库；模型判断答不了、输出格式不对或报错时退回 `run_pipeline`。完整结果只保留最近一次查库的（`SessionState.last_result`），历史里每轮仍只存前 5 行预览
- 评测：`result_qa` 轮增加 `gold_answer_contains`，直接回答时按关键内容判分，退回查库时仍按 `gold_sql` 判分；teacher 模式额外用上一轮标准结果填充 `last_result`

**步骤 5：反问澄清**

- 意图新增 `clarify`。Prompt 只允许在三种情况反问：时间范围完全缺失、比较基准不明、指代对象有多个候选；业务术语已有默认口径的不问
- 新节点 `ask_clarification` 用 `interrupt()` 暂停，`QueryService` 把 `__interrupt__` 转成 `clarify` 事件；接口新增 `resume` 字段，以 `Command(resume=...)` 恢复，回答以“（补充：……）”拼在原话后回到 `contextualize`，每轮最多反问一次
- 已验证的 LangGraph 行为：恢复时节点会重新执行；停在反问处时发来不带 `resume` 的新问题，会丢弃这次反问、从头开始一轮；没有待回答的反问时直接 `Command(resume)` 会以空输入运行而报错，所以 `QueryService` 先用 `aget_state` 检查
- 前端：反问卡片（选项按钮、“跳过，直接提新问题”）、输入框提示、流程图新增“反问澄清”“基于结果回答”两个节点
- 首轮默认不经过改写节点，所以首轮不会反问；`conversation.contextualize_first_turn` 打开后首轮也能反问，代价是每个首轮多一次 LLM 调用
- 评测：新增 2 段需要反问的对话（“最近”没有范围、“那个品牌”有三个候选），标注 `clarify_reply` 扮演用户回答；意图按第一次判断计，用来衡量该不该反问

**评测结果（glm-5.3-flash）**：同一批 20 段对话的追问轮准确率，teacher 模式 92% → 100%，自由对话 88% → 100%。第一阶段的 3 种真实错误都对上了原因：加条件后地区角色用错、“销量”按下单口径（这两种由参考 SQL 修好），“再按平台拆”丢了按月维度（由改写规则区分叠加维度和替换维度修好）。反问是现在最弱的一环：2 段需要反问的对话只在自由对话里成功 1 次，另外还有 1 次不该问却问了。详细解读见 `notebooks/eval_tutorial.ipynb` 第 10 节。

**步骤 6：持久化（暂缓）**：需要新增检查点存储（官方 SQLite 检查点文件，或在 MySQL 里新建独立的库），与“数据库表结构保持与仓库一致”的约定有关，先不做。

---

## 五、后续路线（按投入产出排序）

1. **用真实 LLM 跑基线和消融实验**（第三节），用数据决定下一步优化哪个环节
2. **多轮问数**（第四节）：步骤 1～3 已完成，接下来是第 4 步（`followup` 带参考 SQL、`answer_from_result`）
3. **指标半结构化**：在 `MetricConfig` 中增加 `expr`、`base_table`、`required_filter`（如支付金额必须 `pay_time IS NOT NULL`）。语义检查据此判断“用了支付金额却漏了支付过滤”，这是向 WrenAI 语义层迈出的第一步，代价远小于完整编译器
4. **示例库飞轮**：前端点赞的问答经人工确认后写入 `sql_examples.yaml`，点踩的进入待审核评测题（借鉴 Vanna：只回灌验证过的样本）
5. **行级权限**：用 sqlglot 在 AST 上强制加过滤条件（借鉴 SQLBot），安全规则不交给 Prompt
6. **结果总结节点**（借鉴 DB-GPT 的“取数 → 分析 → 报告”）：在 `run_sql` 之后给出一句话结论和推荐图表，同时为多轮的 `answer_from_result` 提供结果摘要
