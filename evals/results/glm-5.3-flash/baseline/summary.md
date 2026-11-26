# 评测报告 20261126-133122

- 答题方式：`agent`；评测集：`evals/datasets/traps.yaml`；题目数：100
- 配置：few-shot 示例 开；SQL 语义检查 开
- **准确率：89.0%**（89/100）
- 必过题失败：activity_01
- 平均 LLM 调用 6.05 次、19518.5 tokens、耗时 105.27 秒；最终 SQL 仍有语义检查提示的题 1 道

## 分类别

| 类别 | 题数 | 通过 | 准确率 |
| --- | --- | --- | --- |
| activity | 10 | 8 | 80% |
| date_role | 10 | 10 | 100% |
| distinct | 10 | 10 | 100% |
| fanout | 10 | 8 | 80% |
| funnel | 10 | 9 | 90% |
| order_vs_pay | 10 | 8 | 80% |
| refund | 10 | 10 | 100% |
| region_role | 10 | 10 | 100% |
| relative_time | 10 | 7 | 70% |
| snowflake | 10 | 9 | 90% |

## 失败环节分布

| 环节 | 题数 |
| --- | --- |
| 结果不符 | 6 |
| 召回遗漏 | 2 |
| 过滤误删 | 2 |
| 流程异常 | 1 |

## 失败明细

| 题目 | 问法 | 环节 | 说明 |
| --- | --- | --- | --- |
| order_vs_pay_02 | question | 召回遗漏 | tables:dim_date; tables:dim_date |
| order_vs_pay_02 | paraphrase_1 | 召回遗漏 | tables:dim_date; tables:dim_date |
| fanout_04 | question | 结果不符 |  |
| fanout_04 | paraphrase_1 | 结果不符 |  |
| snowflake_03 | paraphrase_1 | 结果不符 |  |
| activity_01 | question | 过滤误删 | tables:fact_order |
| activity_01 | paraphrase_1 | 过滤误删 | tables:fact_order |
| relative_time_02 | paraphrase_1 | 结果不符 |  |
| relative_time_04 | paraphrase_1 | 结果不符 |  |
| relative_time_05 | paraphrase_1 | 流程异常 | OutputParserException: Invalid json output: [   “销售额”，   “销售额同比增长率”，   “同比增长率”，   “同比增速”，   “同比增幅”，   “销售收入”，   “销售金额”，  |
| funnel_03 | paraphrase_1 | 结果不符 |  |
