# 教学数仓 SQL 快照

`dw_snapshot.sql.gz` 是教学数仓的固定版本：建库、11 张表的建表语句（与 `docker/mysql/dw.sql` 一致）和全部数据（约 34 万行，与 `generate_dw_data` 写入的逐表校验和一致）。解压后约 34 MB。

它由 `uv run python -m app.scripts.export_dw_sql` 从固定种子的数据生成器渲染得到，同样的代码每次导出的字节都相同。改了 `app/scripts/dw_generator.py` 或 `dw.sql` 后要重新导出，`tests/test_export_dw_sql.py` 会检查快照是否过期。

## 导入

导入到 compose 里的 MySQL（会**覆盖** `dw` 库的 11 张表，效果等同于运行 `generate_dw_data`）：

```bash
gunzip -c docker/snapshot/dw_snapshot.sql.gz | docker exec -i mysql mysql -uroot -pdili123
```

只想探查、不想动 `dw` 库时，导出一份到其他库名再导入：

```bash
uv run python -m app.scripts.export_dw_sql --database dw_explore -o /tmp/dw_explore.sql.gz
gunzip -c /tmp/dw_explore.sql.gz | docker exec -i mysql mysql -uroot -pdili123
```

进入命令行客户端（`--default-character-set` 保证中文正常显示）：

```bash
docker exec -it mysql mysql --default-character-set=utf8mb4 -uroot -pdili123 dw
```

## 表之间的关系

`dw.sql` 没有声明外键约束，关联关系记在 `conf/meta_config.yaml` 各外键字段的 `ref` 里：

| 外键 | 关联到 |
| --- | --- |
| `fact_order.user_id` | `dim_user.user_id` |
| `fact_order.shop_id` | `dim_shop.shop_id` |
| `fact_order.receiver_region_id` | `dim_region.region_id`（收货城市） |
| `fact_order.order_date_id` / `pay_date_id` | `dim_date.date_id`（下单日期 / 支付日期） |
| `fact_order_item.order_id` | `fact_order.order_id` |
| `fact_order_item.sku_id` | `dim_sku.sku_id` |
| `fact_order_item.activity_id` | `dim_activity.activity_id` |
| `fact_refund.order_item_id` / `order_id` | `fact_order_item.order_item_id` / `fact_order.order_id` |
| `fact_refund.user_id` | `dim_user.user_id` |
| `fact_refund.apply_date_id` | `dim_date.date_id` |
| `fact_user_behavior.user_id` / `sku_id` / `date_id` | `dim_user` / `dim_sku` / `dim_date` |
| `dim_user.region_id` | `dim_region.region_id`（常住城市） |
| `dim_sku.category_id` / `shop_id` | `dim_category.category_id` / `dim_shop.shop_id` |

各表的粒度、口径和设计取舍见 `docs/design/dw-schema-v2.md`，13 个指标的计算公式见 `conf/meta_config.yaml`。

## 探查用的 SQL

```sql
-- 每张表的行数和说明
SELECT table_name, table_rows, table_comment
FROM information_schema.tables WHERE table_schema = 'dw' ORDER BY table_name;

-- 全部字段、类型和注释
SELECT table_name, column_name, column_type, is_nullable, column_comment
FROM information_schema.columns WHERE table_schema = 'dw'
ORDER BY table_name, ordinal_position;

-- 枚举字段的取值分布（换成想看的字段）
SELECT order_status, COUNT(*) FROM fact_order GROUP BY order_status;

-- 订单头与明细对平：订单头 order_amount = total - discount + freight，明细 pay_amount 不含运费
SELECT o.order_id, o.total_amount, o.discount_amount, o.freight_amount, o.order_amount,
       SUM(i.pay_amount) AS items_pay_amount
FROM fact_order o JOIN fact_order_item i ON i.order_id = o.order_id
GROUP BY o.order_id LIMIT 10;

-- 扇出陷阱：订单头金额关联明细后求和，会比真实值大
SELECT (SELECT SUM(order_amount) FROM fact_order WHERE pay_time IS NOT NULL) AS 正确,
       (SELECT SUM(o.order_amount) FROM fact_order o
        JOIN fact_order_item i ON i.order_id = o.order_id
        WHERE o.pay_time IS NOT NULL) AS 扇出后;
```
