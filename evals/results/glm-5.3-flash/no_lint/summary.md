# 评测报告 20261126-141653

- 答题方式：`agent`；评测集：`evals/datasets/traps.yaml`；题目数：50
- 配置：few-shot 示例 开；SQL 语义检查 关
- **准确率：96.0%**（48/50）
- 必过题失败：activity_01
- 平均 LLM 调用 6.0 次、18560.3 tokens、耗时 12.89 秒；最终 SQL 仍有语义检查提示的题 0 道

## 分类别

| 类别 | 题数 | 通过 | 准确率 |
| --- | --- | --- | --- |
| activity | 5 | 4 | 80% |
| date_role | 5 | 5 | 100% |
| distinct | 5 | 5 | 100% |
| fanout | 5 | 4 | 80% |
| funnel | 5 | 5 | 100% |
| order_vs_pay | 5 | 5 | 100% |
| refund | 5 | 5 | 100% |
| region_role | 5 | 5 | 100% |
| relative_time | 5 | 5 | 100% |
| snowflake | 5 | 5 | 100% |

## 失败环节分布

| 环节 | 题数 |
| --- | --- |
| 结果不符 | 1 |
| 过滤误删 | 1 |

## 失败明细

| 题目 | 问法 | 环节 | 说明 |
| --- | --- | --- | --- |
| fanout_04 | question | 结果不符 |  |
| activity_01 | question | 过滤误删 | tables:fact_order |
