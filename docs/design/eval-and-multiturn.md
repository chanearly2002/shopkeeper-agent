# 改进方案：评测体系与多轮问数

本文是 shopkeeper-agent 后续两块重点改进的设计方案：

1. 系统化评测集与自动化回归评测
2. 多轮问数记忆、追问改写与会话管理

建议**先做评测，再做多轮**：没有评测集，多轮改造的效果无法衡量，也无法确认有没有把单轮问数改坏。

---

## 一、参考项目与资料

> 星标数和状态于 2026-11-18 通过 GitHub 搜索核实。

### 开源 Data Agent / ChatBI 项目

| 项目 | 状态 | 值得借鉴的地方 |
| --- | --- | --- |
| [Canner/WrenAI](https://github.com/Canner/WrenAI)（约 1.8 万星） | 活跃 | **语义层（MDL）**：表关系、指标口径、计算字段集中建模，SQL 生成基于语义层而不是裸表；问题改写、SQL 修正、图表生成都是独立的 pipeline，适合对照本项目的节点拆分 |
| [eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT)（约 2 万星） | 活跃 | 范围更大的数据 Agent：SQL 之外还有 Python 分析和报告生成；配套 DB-GPT-Hub 做 Text-to-SQL 微调与评测 |
| [dataease/SQLBot](https://github.com/dataease/SQLBot)（约 7 千星） | 活跃 | 国内团队做的 ChatBI，和本项目场景最接近；值得看它的多轮对话、权限、图表和嵌入集成（MCP、iframe）怎么做 |
| [spring-ai-alibaba/DataAgent](https://github.com/spring-ai-alibaba/DataAgent)（约 2.9 千星） | 活跃 | 基于图编排（Spring AI Alibaba Graph），有 Human-in-the-loop 人工干预、Python 深度分析和报告生成，可对照 LangGraph 的 `interrupt` |
| [OtterMind/Chat2DB](https://github.com/OtterMind/Chat2DB)（约 2.8 万星） | 活跃 | 偏开发者的数据库客户端，NL2SQL 是其中一个功能；可参考 SQL 编辑和结果展示的交互 |
| [vanna-ai/vanna](https://github.com/vanna-ai/vanna)（约 2.4 万星） | **已归档** | “问题-SQL 对”入库作为 few-shot 召回的思路仍有价值，但不建议作为依赖 |
| [eosho/langchain_data_agent](https://github.com/eosho/langchain_data_agent)、[fengyede79/text-to-sql-agent-system](https://github.com/fengyede79/text-to-sql-agent-system) | 小型项目 | 同样基于 LangGraph 的 NL2SQL 实现，后者也做了 SQL 安全校验和有界纠错，可以对照实现细节 |

### 大厂工程实践

- **Uber QueryGPT**（[官方博客](https://www.uber.com/en-us/blog/query-gpt/)）：先用意图分类把问题路由到业务域，再做表召回；大表的 schema 会剪枝。评测集取自真实日志，人工确认每题的意图、所需表和标准 SQL，因此能区分失败出在意图、选表还是 SQL 生成。**本项目的评测归因设计直接借鉴这一点。**
- **Pinterest Text-to-SQL**（[第一代](https://medium.com/pinterest-engineering/how-we-built-text-to-sql-at-pinterest-30bad30dabff)，以及 2026 年的 [Unified Context-Intent Embeddings 总结](https://sysdesign-wiki.pages.dev/sources/2026-03-06-pinterest-unified-context-intent-embeddings-for-scalable-text-to-sql/)）：选表单独做离线评测，并用评测结果调检索权重。第二代改为“按意图召回历史上回答过相似问题的 SQL”。
- **Databricks Genie Benchmarks**（[文档](https://docs.databricks.com/aws/en/genie/benchmarks)，[博客](https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review)）：每个常见问题准备 2～4 种问法、对应同一条标准 SQL；按结果集比对判定，没有标准 SQL 的题目进入人工审核；评测结果随时间留档。**评测集格式直接借鉴。**
- **Snowflake Cortex Analyst**（[多轮与内部架构](https://snowflake.com/engineering-blog/snowflake-cortex-analyst-behind-the-scenes)，[Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)）：多轮对话时，先由一个总结 Agent 把追问**改写成独立完整的问题**，再走单轮流程；经人工验证的“问题-SQL”对存进库里，作为相似问题的参考。**多轮方案的主干就是这个思路。**

### 基准与论文

- **BIRD / BIRD-Interact**（[bird-bench](https://bird-bench.github.io/)，[BIRD-Interact 论文](https://arxiv.org/pdf/2510.05318)，[代码](https://github.com/bird-bench/BIRD-Interact)）：BIRD-Interact 是 ICLR 2026 的多轮交互评测，包含会话模式和 Agent 模式，会注入歧义、要求澄清并追加后续子任务；最强模型的成功率也只有 16%～17% 左右，说明多轮问数确实难。
- **Spider 2.0**（[论文](https://arxiv.org/html/2411.07763v2)）：执行准确率（EX）只要求标准答案的核心列出现在结果中，不要求列顺序、允许多出列。BIRD 则要求内容和列顺序都一致。**两种判定口径都需要了解，本项目建议用宽松口径（见下文）。**
- **CoSQL / SParC**：较早的多轮 Text-to-SQL 数据集，适合参考如何标注“上下文相关的连续问题”。
- **多轮记忆**：[Memory Architectures for Multi-Turn Text-to-SQL](https://arxiv.org/html/2605.26394)（2026，企业场景，300 个会话、1400 轮）对比了不同记忆结构；[Conversational Text-to-SQL under Chain Ambiguity and Intent Drift](https://arxiv.org/pdf/2608.29543) 讨论连续歧义和意图漂移。
- **综述**：[HKUSTDial/NL2SQL_Handbook](https://github.com/HKUSTDial/NL2SQL_Handbook) 持续整理 NL2SQL 的论文、数据集和评测方法。
- **标准答案本身也会出错**：有二手资料称 2026 年初的专家审计发现 BIRD Mini-Dev、Spider 2.0-Snow 中相当比例的题目标注有误（[来源](https://count.co/ai/text-to-sql)，未找到一手出处，仅作提醒）。结论：**公开基准只做参考，回归评测要用自己数仓上人工核对过的题目。**

### 评测与观测工具

- [promptfoo](https://github.com/promptfoo/promptfoo)（约 2.7 万星）：YAML 配置的评测与红队测试，有 GitHub Action，适合把“只读安全”这类用例做成红队测试。
- LangSmith、Langfuse、Arize Phoenix：链路追踪 + 数据集 + 实验对比。本项目已经用 LangChain/LangGraph，接入 LangSmith 或 Langfuse 只需加回调。建议**先自建轻量 runner**，跑通后再决定是否接入平台。

---

## 二、方案一：系统化评测集与自动化回归评测

### 2.1 目标

1. 每次改 Prompt、模型、检索参数或节点逻辑后，**一条命令**就能知道整体准确率是升是降、哪些题从对变错。
2. 出错时能**定位到环节**：召回漏了、过滤删多了、SQL 写错了，还是执行报错。
3. 能挂进 CI 作为门禁，防止明显退化被合入。

### 2.2 指标分层

| 层级 | 指标 | 说明 |
| --- | --- | --- |
| 召回 | 表 / 字段 / 指标 / 取值的召回率 | 标准答案需要的项是否出现在 `merge_retrieved_info` 的输出里。**召回漏掉的，后面不可能补回来** |
| 过滤 | 过滤后的召回率与精确率 | `filter_table` / `filter_metric` 是否删掉了必需项（召回率），是否保留了太多无关项（精确率） |
| SQL | 可执行率、修正后可执行率、平均修正次数 | 首次 SQL 能否通过校验；修正循环的作用有多大 |
| 结果 | **执行准确率（EX）** | 预测 SQL 与标准 SQL 的执行结果是否等价（判定规则见 2.4），这是核心指标 |
| 安全 | 只读违规拦截率 | 对抗性问题（“删掉订单表”）必须 100% 被拦截 |
| 成本 | 端到端耗时、LLM 调用次数、token 数 | 防止“准确率涨了，成本翻倍” |

### 2.3 评测集格式

放在 `evals/datasets/*.yaml`，单轮和多轮使用同一种格式（单轮就是只有一轮的会话）：

```yaml
- id: paid_amount_by_area_2025q1
  tags: [多表, 指标, 分组, 排序, 支付口径]  # 用来按类别统计准确率
  difficulty: medium                      # easy / medium / hard
  today: "2025-12-31"                     # 本题注入的“今天”，保证相对时间可复现
  turns:
    - question: 统计 2025 年第一季度各大区的销售额，并从高到低排序
      paraphrases:                        # 借鉴 Genie：同一个标准 SQL 配 2~4 种问法
        - 25 年 Q1 每个大区卖了多少钱，从高到低排
        - 今年一季度各区域的支付金额排名
      gold_sql: |
        select r.area, sum(o.order_amount) as 支付金额
        from fact_order o
        join dim_region r on o.receiver_region_id = r.region_id
        join dim_date d on o.pay_date_id = d.date_id
        where o.pay_time is not null and d.year = 2025 and d.quarter = 1
        group by r.area
        order by 支付金额 desc
      expect:                             # 用于召回 / 过滤的环节归因
        tables: [fact_order, dim_region, dim_date]
        columns: [fact_order.order_amount, fact_order.pay_time, dim_region.area, dim_date.quarter]
        metrics: [支付金额]
        values: []
      order_matters: true                 # 有 ORDER BY 的题才比较行顺序
      notes: “大区”默认按收货城市统计；若按用户常住城市，属于另一道题
```

几类特殊题目：

- **拒答 / 安全题**：`expect_refusal: true`，例如“把 fact_order 表清空”，期望被只读校验拦截，或模型明确拒绝。
- **空结果题**：例如“2024 年的销售额”，标准答案就是空结果，用来检查模型不会编造数据。
- **歧义题**：`ambiguous: true`，单轮模式下只记录不计分；多轮上线后，期望系统发起澄清。

**覆盖矩阵**：先手写 40～60 道种子题，保证下面每一格至少有 3 道：

| 维度 | 取值 |
| --- | --- |
| 表数量 | 单表 / 两表 join / 三表及以上 |
| 是否用到指标 | 下单金额 / 支付金额 / 客单价 / 退款率等 / 不用指标 |
| 是否用到字段取值 | 地区、品类、品牌、会员等级等枚举值 |
| 时间表达 | 绝对时间 / 相对时间（本月、最近 7 天、去年同期）/ 不限时间 |
| 口径陷阱 | 下单 vs 支付 / 订单头 vs 明细（扇出）/ 退款扣减 / 日期与地区的角色 / 去重计数（见 `dw-schema-v2.md` 第六节） |
| 结果形态 | 单值 / 分组 / TopN / 明细 / 空结果 |
| 特殊 | 安全拒答 / 歧义 |

**扩充来源**：① 用 LLM 基于 `meta_config.yaml` 批量合成问题和 SQL，**必须人工执行并核对后才能入库**；② 线上日志：前端加点赞/点踩，被点踩的问题经人工修正后进入评测集（这也是 Uber 的做法）。

### 2.4 结果判定规则

用同一个数据库连接分别执行标准 SQL 和预测 SQL，比较结果集：

1. **忽略列名和列顺序**：按列的值来匹配。标准答案的每一列，都必须能在预测结果中找到一列内容相同的列；预测结果**允许多出列**（参照 Spider 2.0 的宽松口径——模型多返回一列“订单数”不应判错）。
2. **行**：`order_matters: false` 时按多重集合比较；为 `true` 时按顺序比较。
3. **数值容差**：浮点数按相对误差 1e-6 比较；`Decimal`、`int`、`float` 统一转成 `float`。日期统一转成 ISO 字符串。
4. **结果被截断时**：比较前 `max_result_rows` 行，并在报告中标出。
5. 以上规则都无法判定的（例如开放式问题），才交给 LLM 评审，并单独统计、不进入主指标。

判定逻辑放在 `evals/compare.py`，**要配单元测试**，判定器有错会让整套评测失去意义。

### 2.5 架构

```
evals/
├── datasets/              # 评测集 YAML（版本化）
├── runner.py              # 加载数据集 → 逐题调用图 → 收集中间状态 → 判定 → 写报告
├── compare.py             # 结果集等价判定
├── attribution.py         # 失败归因：召回 / 过滤 / 生成 / 执行
├── reports/               # 每次运行的 JSONL 明细 + Markdown 汇总（不入库或只入库 baseline）
└── baseline.json          # 主干分支上一次的各项指标，用于回归比较
```

**runner 的要点**：

- **直接调用 `graph`，不走 HTTP**，复用 `app/agent/graph.py` 末尾那套依赖组装方式。
- 用 `graph.astream(..., stream_mode=["updates", "custom"])` 拿到**每个节点的输出**，这样不用改节点代码，就能得到召回结果、过滤结果、每一版 SQL 和修正次数。
- 题目之间并发执行（例如 `asyncio.Semaphore(4)`），每题独立的 MySQL Session。
- 用 LangChain 回调（`UsageMetadataCallbackHandler` 或自定义）统计每题的 LLM 调用次数和 token。

**失败归因**（按顺序判断，命中第一条即停）：

1. `expect.tables` / `columns` / `metrics` / `values` 不在合并结果里 → **召回失败**
2. 在合并结果里，但被过滤节点删掉了 → **过滤失败**
3. SQL 修正次数用完仍未通过校验 → **生成失败（不可执行）**
4. SQL 可执行，但结果不等价 → **生成失败（语义错误）**

报告按“标签 × 归因”交叉统计，例如“三表 join 题的失败有 70% 出在过滤节点”，比单看总准确率有用得多。

### 2.6 确定性与成本：录制回放

LLM 调用即使 `temperature=0` 也不完全确定，而且每次全量跑都要花钱。建议做两种模式：

- **live 模式**：真实调用 LLM 和 Embedding。改了 Prompt、模型或检索策略时使用，夜间定时跑一次。
- **replay 模式**：通过 `langchain_core.globals.set_llm_cache` 挂一个持久化的 LLM 缓存，按“Prompt 全文”命中。可以安装 `langchain-community` 使用其中的 `SQLiteCache`，也可以继承 `langchain_core.caches.BaseCache` 自己写一个基于 JSON 或 SQLite 的实现（几十行代码，少一个依赖）。Embedding 同理，给 `embedding_client` 包一层按文本缓存（LangChain 1.x 中的 `CacheBackedEmbeddings` 已移到 `langchain-classic` 包，自己包一层更简单）。**只改了代码逻辑、Prompt 没变时，回放结果应完全一致**，适合在每个 PR 上跑。缓存文件可以按评测集版本保存为 CI 产物。

### 2.7 回归门禁

`runner.py --compare baseline.json` 输出差异，满足任一条件即判为退化、命令以非 0 退出：

- 整体 EX 下降超过 2 个百分点（阈值可配置；题目少时阈值要放宽，40 题时 1 题就是 2.5%）
- 任一标记为 `must_pass: true` 的题目失败（核心场景 + 全部安全题）
- 平均 LLM 调用数或 token 数上升超过 30%

报告中列出“由对变错”和“由错变对”的题目 id，评审 PR 时直接看这份清单。

### 2.8 CI 接入

在 GitHub Actions 中：

1. `pytest`：单元测试，无外部依赖，每个 PR 都跑。
2. `evals (replay)`：用 `services:` 起 MySQL、Qdrant、ES，跑知识库构建（构建现在是幂等的）和 replay 评测。Embedding 模型较大，建议同样走缓存，不在 CI 里启动 TEI。
3. `evals (live)`：定时任务 + 手动触发，需要在仓库 Secrets 里配置 `LLM_API_KEY`，结果通过后更新 `baseline.json`。

### 2.9 需要先做的代码改动

- **把 `agent.today` 从全局配置改为每次请求的上下文**（放进 `DataAgentContext` 或初始 State）。评测需要每题指定自己的日期，多轮会话也需要固定日期。
- 节点里的 `JsonOutputParser` 解析失败目前会直接中断整条流程，需要统一的重试或降级处理，否则评测会被偶发的格式错误干扰。
- `extract_keywords` 用 `set` 去重，关键词顺序不稳定，会让 replay 模式的缓存命中率下降；改成保序去重。

### 2.10 实施步骤

| 步骤 | 产出 | 预计 |
| --- | --- | --- |
| 1 | `compare.py` + 单元测试（**已完成**）；`today` 改为请求级（未完成） | 0.5 天 |
| 2 | 种子题：已完成 10 类业务口径陷阱 × 5 题（`evals/datasets/traps.yaml`），每题都在数仓上校验过；安全题、空结果题、歧义题尚未加入 | 1～2 天 |
| 3 | `runner.py`：逐题执行、收集中间状态、判定、输出 JSONL 与 Markdown 报告 | 1 天 |
| 4 | 失败归因 + 按标签统计 | 0.5 天 |
| 5 | replay 缓存 + baseline 对比 + 退出码 | 0.5 天 |
| 6 | GitHub Actions：pytest + replay 评测 | 0.5 天 |
| 7 | 前端点赞/点踩，被点踩的问题沉淀进待审核题库 | 1 天 |

---

## 三、方案二：多轮问数、追问改写与会话管理

### 3.1 要支持的对话场景

| 类型 | 例子（接在“统计 2025 年 Q1 各大区的销售额”之后） | 处理方式 |
| --- | --- | --- |
| 省略 / 指代 | “那华东呢？” | 改写成完整问题，走单轮流程 |
| 追加条件 | “只看黄金会员” | 改写，或在上一轮 SQL 上增量修改 |
| 换维度 / 下钻 | “按月份拆开看” | 同上 |
| 针对上一轮结果追问 | “排第一的是哪个区？占比多少？” | **不必重新查库**，直接基于上一轮结果作答 |
| 纠正 | “不对，我要的是销量不是金额” | 改写时用新口径替换旧口径 |
| 换话题 | “会员等级有哪些？” | 识别为新问题，不带入上文 |
| 需要澄清 | “看看最近的情况” | 反问用户，等用户回答后继续 |

### 3.2 整体架构

**主干思路参考 Snowflake Cortex Analyst：在图的入口加一个“理解”节点，把追问改写成独立完整的问题，后面的召回 → SQL 链路基本不用动。** 改写结果也要展示给用户（“我理解为：……”），用户可以直接纠正，这是多轮系统最重要的可解释性来源。

```
START
  └─ load_session ─ contextualize_query ─┬─(new / refine / drill_down)─ extract_keywords ─ ...原单轮链路... ─ run_sql ─┐
                                         ├─(result_qa)─ answer_from_result ────────────────────────────────────────┤
                                         ├─(clarify)── ask_clarification（interrupt，等用户回答后恢复）            │
                                         └─(chitchat / 越界)─ direct_reply ─────────────────────────────────────────┤
                                                                                                        save_turn ─ END
```

### 3.3 新增节点

**`contextualize_query`（追问改写 + 意图识别）**

- 输入：当前问题；最近 K 轮（建议 K=3）的 `{原问题, 改写后问题, SQL, 结果列名, 行数, 前 5 行}`；更早轮次的摘要。
- 输出（JSON）：

  ```json
  {
    "intent": "refine",
    "standalone_question": "统计 2025 年第一季度华东大区的销售额",
    "based_on_turn": 2,
    "clarify_question": null
  }
  ```

  `intent` 取值：`new` / `refine` / `drill_down` / `result_qa` / `clarify` / `chitchat`。
- 后续所有节点改用 `standalone_question` 替代原来的 `query`；原问题保留在 State 里，用于展示和评测。
- **第一轮直接跳过 LLM 调用**（没有上文），单轮问数的成本和效果不受影响。
- Prompt 里放 8～10 个覆盖上表各场景的 few-shot 示例。Snowflake 的经验是：靠示例教会模型判断“这句话是否依赖上文”最有效。

**`answer_from_result`**：上一轮结果不大时（例如 ≤ 200 行），直接把结果交给 LLM 回答“哪个最高”“占比多少”这类问题；结果太大就退回 SQL 链路，生成一条新查询。

**`ask_clarification`**：用 LangGraph 的 `interrupt()` 暂停执行，向前端发 `{"type": "clarify", "question": "...", "options": [...]}`，用户回答后用 `Command(resume=...)` 继续执行。这需要 checkpointer（见 3.4）。

**`generate_sql` 增强**：`intent` 为 `refine` / `drill_down` 时，把上一轮 SQL 作为“参考 SQL”放进 Prompt，提示模型“在此基础上修改”。增量修改比从头生成更稳定，也更容易保持口径一致。

### 3.4 记忆分层

| 层 | 存什么 | 存在哪 | 生命周期 |
| --- | --- | --- | --- |
| 会话内短期记忆 | 最近 K 轮的问题、改写、SQL、结果摘要；更早轮次的滚动摘要 | 自建表 `conversation_turn`（Meta MySQL）；需要 interrupt 时再加 LangGraph checkpointer | 跟随会话 |
| 用户长期记忆 | 用户偏好：默认时间范围、常用口径（“销售额默认指支付金额”）、常看的维度 | LangGraph Store 或自建表 `user_memory` | 跨会话 |
| 组织知识 | 人工验证过的“问题-SQL”对（Verified Queries） | Qdrant 新集合 + Meta MySQL | 长期，人工维护 |

说明：

- **为什么短期记忆用自建表而不是只用 checkpointer**：checkpointer 存的是整份图状态（包含召回结果、表结构等大对象），不方便查询，也不方便前端展示历史。会话历史本身就是业务数据，建议单独建表；checkpointer 只负责 interrupt 的暂停与恢复。LangGraph 官方提供 Postgres、SQLite 的 checkpointer，项目目前只用 MySQL，用 MySQL 需要社区实现或自己实现，可以先用内存实现 `InMemorySaver` 跑通流程，再换成持久化实现（SQLite 版需要安装 `langgraph-checkpoint-sqlite`，Postgres 版需要 `langgraph-checkpoint-postgres`）。
- **结果不要整份存进上下文**：只存列名、行数、前 5 行，避免 token 膨胀。
- **滚动摘要**：会话超过 K 轮时，把更早的轮次压缩成一段摘要（“用户在分析 2025 年一季度各区域销售，关注华东和金卡会员”）。
- **Verified Queries**：用户点赞的问答经人工确认后，作为 few-shot 示例召回进 `generate_sql` 的 Prompt。这正好和评测方案中“点赞/点踩 → 题库”共用一套数据。

### 3.5 数据模型（Meta MySQL 新增两张表）

```sql
CREATE TABLE conversation (
    id          VARCHAR(36) PRIMARY KEY,
    user_id     VARCHAR(64),
    title       VARCHAR(200),               -- 取首轮问题或由 LLM 生成
    summary     TEXT,                       -- 滚动摘要
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE conversation_turn (
    id                  VARCHAR(36) PRIMARY KEY,
    conversation_id     VARCHAR(36) NOT NULL,
    turn_index          INT NOT NULL,
    question            TEXT,               -- 用户原话
    standalone_question TEXT,               -- 改写后的问题
    intent              VARCHAR(32),
    sql_text            TEXT,
    result_preview      JSON,               -- 列名 + 前 N 行 + 行数 + 是否截断
    status              VARCHAR(16),        -- success / error / clarify
    error               TEXT,
    feedback            TINYINT,            -- 1 点赞 / -1 点踩 / NULL
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_conv_turn (conversation_id, turn_index)
);
```

按照现有分层，需要同步新增 ORM 模型、实体、Mapper，以及 `ConversationRepository`。

### 3.6 API 与 SSE 协议

| 接口 | 说明 |
| --- | --- |
| `POST /api/conversations` | 新建会话，返回 `conversation_id` |
| `GET /api/conversations` | 会话列表（侧边栏） |
| `GET /api/conversations/{id}/turns` | 历史轮次（刷新页面后恢复） |
| `DELETE /api/conversations/{id}` | 删除会话 |
| `POST /api/query` | 请求体新增 `conversation_id`（可选，不传则自动新建会话）和 `resume`（回答澄清问题时使用） |
| `POST /api/turns/{id}/feedback` | 点赞 / 点踩 |

SSE 新增事件类型（同步修改 `frontend/src/types/agent.ts`）：

```json
{"type": "session", "conversation_id": "...", "turn_id": "..."}
{"type": "rewrite", "standalone_question": "...", "intent": "refine"}
{"type": "clarify", "question": "你说的“最近”是指最近 7 天还是本月？", "options": ["最近 7 天", "本月"]}
{"type": "answer", "text": "华东的销售额最高，占比 38.2%"}
```

注意：`App.tsx` 目前把所有非 progress/result 的事件都当作错误处理，新增事件类型时要一并修改；`StepRail.tsx` 也要加上新节点。

### 3.7 前端

- 侧边栏：会话列表，替代现在“刷新即丢失”的内存消息列表。
- 每轮回答顶部显示“我理解为：{standalone_question}”，提供“理解有误”按钮，点击后预填一条纠正消息。
- 澄清卡片：显示选项按钮，点击后带 `resume` 再次请求。
- 每轮回答下方加点赞/点踩，数据同时用于 Verified Queries 和评测题库。

### 3.8 多轮评测

沿用 2.3 的格式，一道题有多轮 `turns`，每一轮都标注：

```yaml
- id: mt_region_drilldown
  tags: [多轮, 省略, 下钻]
  today: "2025-12-31"
  turns:
    - question: 统计 2025 年第一季度各大区的销售额
      gold_sql: ...
    - question: 那华东呢，按月拆开
      gold_standalone: 统计 2025 年第一季度华东大区每个月的销售额
      gold_intent: drill_down
      gold_sql: ...
    - question: 哪个月最高
      gold_intent: result_qa
      gold_answer_contains: ["3 月"]
```

指标：

- **意图准确率**：`intent` 与 `gold_intent` 一致的比例。
- **改写准确率**：把改写后的问题单独走一遍单轮流程，结果与 `gold_sql` 执行等价即判对。用执行结果判定，不需要逐字比对改写文本。
- **轮次 EX**：每一轮单独计算执行准确率。**评测时上一轮使用标准答案作为历史**（teacher forcing），避免前一轮的错误连累后面所有轮次。
- **会话完全正确率**：整段会话每一轮都对的比例（不使用 teacher forcing，反映真实体验）。

多轮题库可以参考 CoSQL、SParC 的标注方式，以及 BIRD-Interact 中“注入歧义 + 追加子任务”的出题思路。

### 3.9 实施步骤

| 步骤 | 产出 | 预计 |
| --- | --- | --- |
| 1 | 建表 + ORM/实体/Mapper/Repository；`/api/query` 支持 `conversation_id`，每轮落库 | 1 天 |
| 2 | 前端会话列表与历史加载 | 1 天 |
| 3 | `contextualize_query` 节点 + Prompt + `rewrite` 事件，下游改用 `standalone_question` | 1 天 |
| 4 | 多轮评测题 20～30 段，接入方案一的 runner | 1 天 |
| 5 | `refine` / `drill_down` 时把上一轮 SQL 作为参考 | 0.5 天 |
| 6 | `answer_from_result` 分支 | 0.5 天 |
| 7 | checkpointer + `interrupt` 澄清流程 + 前端澄清卡片 | 1.5 天 |
| 8 | 滚动摘要、用户长期记忆、Verified Queries 召回 | 2 天 |

每完成一步就跑一次评测，确认单轮准确率没有下降，多轮指标在上升。

---

## 四、两块方案的衔接

```
种子评测集 ──► runner + 判定 + 归因 ──► baseline ──► CI 门禁
     ▲                                                   │
     │                                                   ▼
 点赞/点踩 ◄── 前端多轮会话 ◄── contextualize_query ◄── 多轮评测题
     │
     └──► Verified Queries（few-shot 召回）
```

用户反馈同时喂给评测集和 Verified Queries，评测集又约束每一次多轮改造，两块工作互相支撑。
