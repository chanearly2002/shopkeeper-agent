# 多轮评测报告 20261126-143225

- 答题方式：`agent`；模式：teacher forcing（历史用标准答案填充）；评测集：`evals/datasets/multiturn.yaml`
- **整段对话成功率：86.4%**（19/22）
- **追问轮准确率：92.3%**（24/26）；全部轮次准确率 93.8%（45/48）
- 意图准确率：92.3%（26 个追问轮）
- 必过对话失败：无
- 每轮平均 LLM 调用 6.33 次、17917.5 tokens、耗时 27.81 秒
- 直接基于上一轮结果回答 2 轮，发起反问 2 轮

> teacher forcing 下每一轮都基于标准历史作答，整段对话成功率只说明“每一轮单独都答对”，真实体验以自由对话模式为准

## 意图混淆

| 标准 → 预测 | 次数 |
| --- | --- |
| followup→clarify | 1 |
| clarify→followup | 1 |

## 分类别

| 类别 | 对话数 | 整段成功 | 追问轮准确率 |
| --- | --- | --- | --- |
| add_filter | 4 | 4（100%） | 4/4（100%） |
| change_dim | 4 | 4（100%） | 5/5（100%） |
| clarify | 2 | 0（0%） | 0/2（0%） |
| correction | 3 | 3（100%） | 3/3（100%） |
| ellipsis | 4 | 4（100%） | 6/6（100%） |
| result_ref | 3 | 2（67%） | 4/4（100%） |
| topic_switch | 2 | 2（100%） | 2/2（100%） |

## 失败环节分布

| 环节 | 轮数 |
| --- | --- |
| 流程异常 | 2 |
| 意图错误 | 1 |

## 失败明细

| 对话 | 轮次 | 环节 | 意图（标准/预测） | 改写（预测 / 标准） | 说明 |
| --- | --- | --- | --- | --- | --- |
| mt_result_ref_03 | 1 | 流程异常 | new/new |  | Invalid json output: [   “支付订单数” ] For troubleshooting, visit: https://docs.langchain.com/oss/python/langchain/errors/OU |
| mt_clarify_01 | 2 | 意图错误 | clarify/followup | 最近华南大区的支付订单数是多少？ / 最近 7 天华南大区的支付订单数是多少？ |  |
| mt_clarify_02 | 2 | 流程异常 | clarify/clarify | 2025 年苹果品牌每个月的销售额是多少？ / 2025 年苹果品牌每个月的销售额 | 反问：你想看哪个品牌的？；Invalid json output: [   “支付金额” ] For troubleshooting, visit: https://docs.langchain.com/oss/python/langcha |
