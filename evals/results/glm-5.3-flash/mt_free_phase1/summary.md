# 多轮评测报告 20261126-142046

- 答题方式：`agent`；模式：自由对话（使用模型自己的历史）；评测集：`evals/datasets/multiturn.yaml`
- **整段对话成功率：80.0%**（16/20）
- **追问轮准确率：87.5%**（21/24）；全部轮次准确率 90.9%（40/44）
- 意图准确率：100.0%（24 个追问轮）
- 必过对话失败：mt_add_filter_01, mt_change_dim_01
- 每轮平均 LLM 调用 6.52 次、17995.3 tokens、耗时 8.5 秒

## 分类别

| 类别 | 对话数 | 整段成功 | 追问轮准确率 |
| --- | --- | --- | --- |
| add_filter | 4 | 3（75%） | 3/4（75%） |
| change_dim | 4 | 3（75%） | 4/5（80%） |
| correction | 3 | 2（67%） | 2/3（67%） |
| ellipsis | 4 | 4（100%） | 6/6（100%） |
| result_ref | 3 | 2（67%） | 4/4（100%） |
| topic_switch | 2 | 2（100%） | 2/2（100%） |

## 失败环节分布

| 环节 | 轮数 |
| --- | --- |
| 结果不符 | 2 |
| 过滤误删 | 1 |
| 流程异常 | 1 |

## 失败明细

| 对话 | 轮次 | 环节 | 意图（标准/预测） | 改写（预测 / 标准） | 说明 |
| --- | --- | --- | --- | --- | --- |
| mt_add_filter_01 | 2 | 结果不符 | followup/followup | 2025 年钻石会员在各大区的支付买家数 / 2025 年钻石会员在各大区的支付买家数 |  |
| mt_change_dim_01 | 3 | 过滤误删 | followup/followup | 2025 年第二季度各平台的销售额是多少？ / 2025 年第二季度每个月各平台的销售额 | tables:dim_date |
| mt_result_ref_03 | 1 | 流程异常 | new/new |  | Invalid json output: [   “支付订单数” ] For troubleshooting, visit: https://docs.langchain.com/oss/python/langchain/errors/OU |
| mt_correction_02 | 2 | 结果不符 | followup/followup | 2025 年美妆个护类目的销量是多少？ / 2025 年美妆个护类目的销量是多少件？ |  |
