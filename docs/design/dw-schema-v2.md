# 教学数仓 v2：表结构方案（待确认）

> 状态：**方案稿，尚未实施**。确认后再同步修改 `docker/mysql/dw.sql`、`conf/meta_config.yaml`、数据生成脚本和相关代码。

## 一、为什么要换数仓

现有数仓只有 5 张表、115 条订单、一个季度的数据，订单表连状态和优惠都没有。用它做评测有三个问题：

1. **业务口径单一**：没有“已取消”“未支付”“退款”，GMV 只能是 `SUM(order_amount)`，测不出模型能否区分“下单金额”和“支付金额”。
2. **结构太简单**：只有一个事实表，而且是订单粒度，测不出真实数仓里最常见的错误——**订单头和订单明细 join 后金额被重复计算（扇出）**。
3. **数据太少**：大多数问题的答案只有一两行，写错的 SQL 也很容易“碰巧”算对。

新方案要达到的目标是：**SQL 语法正确、能执行，但业务口径错了，结果就一定不对。** 这样评测才能真正衡量 Agent 的业务理解能力。

## 二、参考来源

| 来源 | 借鉴的地方 |
| --- | --- |
| 阿里天池 淘宝用户行为数据集 UserBehavior（[阿里云文档](https://help.aliyun.com/zh/dlf/dlf-1-0/use-cases/dlf-data-exploration-taobao-user-behavior-analysis)） | 行为事实表：用户 × 商品 × 类目 × 行为类型（浏览 / 收藏 / 加购 / 购买）× 时间，用于计算转化漏斗 |
| 京东 JData 竞赛数据（[赛题代码与说明](https://uithub.com/hecongqing/2017-jdata-competition)） | 用户表含年龄、性别、用户等级、注册时间；商品带类目与品牌；行为表与评论表分开 |
| Olist 巴西电商公开数据集（[数据说明](https://pavelgrigoryevds.github.io/olist-deep-dive/contents/01_data_description_and_exploration/01_data_description.html)） | 订单头 orders 与订单明细 order_items 分表，另有支付、评价、卖家、商品等表——**这是“订单头 + 明细”双粒度事实表的典型结构** |
| 阿里 OneData / DWD 建模实践（[阿里云 DWD 层开发](https://developer.aliyun.com/article/1532415)，[Dataphin 明细层](https://help.aliyun.com/zh/dataphin/fullmanaged/getting-started/the-detail-data-layer)） | 按业务过程建事实表（下单、支付成功、退单分开）；先声明粒度再定维度和度量；常用维度属性退化进事实表 |

## 三、整体模型

**4 张事实表 + 7 张维度表**，以星型模型为主，类目做成雪花结构（商品 → 类目）。

```
                       dim_date ──────────────┬──────────────┬─────────────┐
                    (下单日/支付日/行为日/申请日)  │              │             │
                                              │              │             │
 dim_region ─(收货城市)─ fact_order ─(1:N)─ fact_order_item ─(1:N)─ fact_refund
     │                    │    │                 │    │
 (常住城市)             dim_shop  dim_user        │   dim_activity
     │                    ▲       │              │
   dim_user ◄─────────────┼───────┘              ▼
     │                    └────────────────── dim_sku ──► dim_category
     └──────────── fact_user_behavior ──────────► ▲
```

| 表 | 类型 | 粒度（一行代表什么） | 预计行数 |
| --- | --- | --- | --- |
| `fact_order` | 事实 | 一个用户在一个店铺下的一笔订单（类似淘宝按店铺拆单后的订单） | 约 6 万 |
| `fact_order_item` | 事实 | 订单中的一个 SKU | 约 9 万 |
| `fact_refund` | 事实 | 针对某个订单明细的一次退款申请 | 约 5 千 |
| `fact_user_behavior` | 事实 | 用户对某个 SKU 的一次行为（浏览 / 收藏 / 加购 / 下单） | 约 20 万 |
| `dim_date` | 维度 | 一天 | 731 |
| `dim_region` | 维度 | 一个城市 | 约 40 |
| `dim_user` | 维度 | 一个用户 | 约 3,000 |
| `dim_category` | 维度 | 一个三级（叶子）类目 | 约 60 |
| `dim_shop` | 维度 | 一个店铺 | 约 40 |
| `dim_sku` | 维度 | 一个 SKU（同款的不同规格是不同 SKU） | 约 400 |
| `dim_activity` | 维度 | 一次营销活动 | 约 15 |

**时间范围**：2024-01-01 ～ 2025-12-31，完整两年。这样可以支持同比、环比，两年的 618 和双 11 都在范围内。`agent.today` 改为 `2025-12-31`。

## 四、表结构明细

金额统一用 `DECIMAL(12,2)`，单位为元。枚举值统一用中文，方便取值召回，也方便模型生成 `WHERE` 条件。

### 4.1 维度表

#### `dim_date` 日期维度

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date_id` | INT, PK | 日期键，格式 `yyyyMMdd`，如 20250618 |
| `full_date` | DATE | 日期 |
| `year` | SMALLINT | 年 |
| `quarter` | TINYINT | 季度，1～4（**现在是 `'Q1'` 字符串，改为数字**） |
| `month` | TINYINT | 月 |
| `day` | TINYINT | 日 |
| `week_of_year` | TINYINT | ISO 周序号 |
| `day_of_week` | TINYINT | 星期几，1 = 周一 … 7 = 周日 |
| `is_weekend` | TINYINT(1) | 是否周末 |
| `is_holiday` | TINYINT(1) | 是否法定节假日 |
| `holiday_name` | VARCHAR(20) | 节假日名称：元旦、春节、清明、劳动节、端午、中秋、国庆，非假日为 NULL |

#### `dim_region` 地区维度（城市级）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `region_id` | VARCHAR(20), PK | 城市编码 |
| `province` | VARCHAR(20) | 省份，如“浙江省” |
| `city` | VARCHAR(20) | 城市，如“杭州市” |
| `city_tier` | VARCHAR(10) | 城市等级：一线 / 新一线 / 二线 / 三线及以下 |
| `area` | VARCHAR(10) | 大区：华东 / 华北 / 华南 / 华中 / 西南 / 西北 / 东北 |

#### `dim_user` 用户维度

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | VARCHAR(20), PK | 用户 ID |
| `user_name` | VARCHAR(50) | 用户昵称 |
| `gender` | VARCHAR(4) | 性别：男 / 女 / 未知 |
| `age_range` | VARCHAR(10) | 年龄段：18-24 / 25-29 / 30-34 / 35-39 / 40-49 / 50 及以上 |
| `region_id` | VARCHAR(20), FK → `dim_region` | **常住城市**（注意和订单的收货城市区分） |
| `member_level` | VARCHAR(10) | 会员等级：普通会员 / 银卡 / 金卡 / 铂金 / 钻石 |
| `is_plus` | TINYINT(1) | 是否付费会员（类似 88VIP、京东 PLUS） |
| `register_date` | DATE | 注册日期 |
| `register_channel` | VARCHAR(10) | 注册渠道：APP / 小程序 / PC / H5 |

#### `dim_category` 类目维度（三级类目）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `category_id` | VARCHAR(20), PK | 三级（叶子）类目 ID |
| `category_l1` | VARCHAR(20) | 一级类目：手机数码 / 家用电器 / 服饰鞋包 / 美妆个护 / 食品饮料 / 母婴 / 家居日用 |
| `category_l2` | VARCHAR(20) | 二级类目，如“手机通讯” |
| `category_l3` | VARCHAR(20) | 三级类目，如“手机” |

#### `dim_shop` 店铺维度

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `shop_id` | VARCHAR(20), PK | 店铺 ID |
| `shop_name` | VARCHAR(50) | 店铺名称，如“小米官方旗舰店” |
| `shop_type` | VARCHAR(10) | 店铺类型：自营 / 旗舰店 / 专营店 / 专卖店 |
| `main_category_l1` | VARCHAR(20) | 主营一级类目 |
| `shop_score` | DECIMAL(3,2) | 店铺综合评分，4.00～5.00 |
| `open_date` | DATE | 开店日期 |

#### `dim_sku` 商品维度

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sku_id` | VARCHAR(20), PK | SKU ID |
| `spu_id` | VARCHAR(20) | SPU ID（同一款商品的不同规格共享一个 SPU） |
| `sku_name` | VARCHAR(100) | 商品名称（含规格），如“iPhone 16 Pro 256GB 黑色钛金属” |
| `category_id` | VARCHAR(20), FK → `dim_category` | 三级类目 |
| `brand_name` | VARCHAR(30) | 品牌 |
| `shop_id` | VARCHAR(20), FK → `dim_shop` | 所属店铺 |
| `list_price` | DECIMAL(12,2) | 标价（划线价） |
| `cost_price` | DECIMAL(12,2) | 成本价，用于计算毛利 |
| `launch_date` | DATE | 上架日期 |
| `sku_status` | VARCHAR(10) | 状态：在售 / 下架 |

#### `dim_activity` 营销活动维度

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `activity_id` | VARCHAR(20), PK | 活动 ID |
| `activity_name` | VARCHAR(50) | 活动名称，如“2025 年 618 大促”“2024 年双 11” |
| `activity_type` | VARCHAR(10) | 活动类型：大促 / 满减 / 直降 / 秒杀 / 优惠券 |
| `start_date` | DATE | 开始日期 |
| `end_date` | DATE | 结束日期 |

### 4.2 事实表

#### `fact_order` 订单事实表（订单粒度）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `order_id` | VARCHAR(30), PK | 订单号 |
| `user_id` | FK → `dim_user` | 下单用户 |
| `shop_id` | FK → `dim_shop` | 店铺 |
| `receiver_region_id` | FK → `dim_region` | **收货城市**（同一张 `dim_region` 被用户常住地和订单收货地两种角色使用） |
| `order_date_id` | FK → `dim_date` | 下单日期 |
| `order_time` | DATETIME | 下单时间 |
| `pay_date_id` | FK → `dim_date`，可空 | 支付日期，未支付为 NULL（**同一张 `dim_date` 有多种日期角色**） |
| `pay_time` | DATETIME，可空 | 支付时间 |
| `platform` | VARCHAR(10) | 下单终端：APP / 小程序 / PC / H5 |
| `payment_method` | VARCHAR(10)，可空 | 支付方式：支付宝 / 微信支付 / 银行卡 / 花呗分期 |
| `order_status` | VARCHAR(10) | 订单状态：待支付 / 已取消 / 已支付 / 已发货 / 已完成（退款单独记在 `fact_refund`，不改订单状态） |
| `item_quantity` | INT | 商品总件数 |
| `total_amount` | DECIMAL | 商品总额 = Σ 明细 `item_amount` |
| `discount_amount` | DECIMAL | 优惠总额 = Σ 明细 `discount_amount` |
| `freight_amount` | DECIMAL | 运费 |
| `order_amount` | DECIMAL | 订单应付金额 = `total_amount − discount_amount + freight_amount`；只有 `pay_time` 不为空时才是实际支付的金额 |

#### `fact_order_item` 订单明细事实表（订单行粒度）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `order_item_id` | VARCHAR(30), PK | 订单明细 ID |
| `order_id` | FK → `fact_order` | 所属订单（下单时间、状态、用户都要通过订单表获取） |
| `sku_id` | FK → `dim_sku` | 商品 |
| `activity_id` | FK → `dim_activity`，可空 | 参与的活动，未参与为 NULL |
| `quantity` | INT | 购买件数 |
| `unit_price` | DECIMAL | 成交单价（可能低于标价） |
| `item_amount` | DECIMAL | 商品金额 = `unit_price × quantity` |
| `discount_amount` | DECIMAL | 分摊到本行的优惠 |
| `pay_amount` | DECIMAL | 本行实付金额 = `item_amount − discount_amount`，不含运费 |

#### `fact_refund` 退款事实表（退款申请粒度）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `refund_id` | VARCHAR(30), PK | 退款单号 |
| `order_item_id` | FK → `fact_order_item` | 退款的订单明细 |
| `order_id` | FK → `fact_order` | 所属订单（冗余，方便关联） |
| `user_id` | FK → `dim_user` | 申请用户 |
| `apply_date_id` | FK → `dim_date` | 申请日期 |
| `apply_time` | DATETIME | 申请时间 |
| `refund_type` | VARCHAR(10) | 退款类型：仅退款 / 退货退款 |
| `refund_reason` | VARCHAR(20) | 退款原因：质量问题 / 尺码不合适 / 不喜欢 / 发错货 / 物流太慢 / 其他 |
| `refund_status` | VARCHAR(10) | 退款状态：退款中 / 退款成功 / 已拒绝 / 已撤销 |
| `refund_quantity` | INT | 退款件数 |
| `refund_amount` | DECIMAL | 退款金额 |
| `finish_time` | DATETIME，可空 | 退款完成时间 |

#### `fact_user_behavior` 用户行为事实表（参考淘宝 UserBehavior）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `behavior_id` | BIGINT, PK | 行为 ID |
| `user_id` | FK → `dim_user` | 用户 |
| `sku_id` | FK → `dim_sku` | 商品 |
| `behavior_type` | VARCHAR(10) | 行为类型：浏览 / 收藏 / 加购 / 下单 |
| `behavior_time` | DATETIME | 行为时间 |
| `date_id` | FK → `dim_date` | 行为日期 |
| `platform` | VARCHAR(10) | 终端：APP / 小程序 / PC / H5 |

## 五、指标口径（写进 `meta_config.yaml`）

| 指标 | 别名 | 计算口径 |
| --- | --- | --- |
| 下单金额（GMV） | GMV、拍下金额、成交总额 | `SUM(fact_order.order_amount)`，**包含未支付和已取消的订单**（阿里“拍下”口径） |
| 支付金额 | 成交额、销售额、GSV | `SUM(fact_order.order_amount)`，限 `pay_time IS NOT NULL` |
| 净支付金额 | 净销售额、实收 | 支付金额 − 退款成功的 `refund_amount` |
| 下单订单数 | 订单量 | `COUNT(fact_order.order_id)` |
| 支付订单数 | 成交订单数 | `COUNT(order_id)`，限已支付 |
| 支付买家数 | 成交用户数、购买人数 | `COUNT(DISTINCT user_id)`，限已支付 |
| 客单价 | 人均消费 | 支付金额 ÷ 支付买家数 |
| 笔单价（AOV） | 平均订单金额 | 支付金额 ÷ 支付订单数 |
| 件单价（ASP） | 平均售价 | 已支付订单明细 `SUM(pay_amount) ÷ SUM(quantity)` |
| 毛利 | 毛利额 | 已支付订单明细 `SUM(pay_amount − cost_price × quantity)` |
| 退款率 | 退款金额占比 | 退款成功金额 ÷ 支付金额 |
| 浏览转化率 | 购买转化率、访购率 | 支付买家数 ÷ 浏览用户数（同一时间范围内） |
| 复购率 | 回购率 | 统计期内支付订单数 ≥ 2 的买家数 ÷ 支付买家数 |

## 六、这套结构能测出哪些“语法对、业务错”的问题

这是这次改造的重点。下面每一类错误，现有的 `validate_sql`（只检查只读和 `EXPLAIN`）都**拦不住**，只有评测集里的标准 SQL 对比才能发现：

| 陷阱 | 示例问题 | 常见错误写法 |
| --- | --- | --- |
| 下单与支付口径 | “2025 年 618 期间的销售额” | 没加 `pay_time IS NOT NULL`，把未支付、已取消的订单也算进去 |
| 订单头 / 明细扇出 | “按一级类目统计支付金额” | 把 `fact_order` 和 `fact_order_item` join 后对 `order_amount` 求和，金额被放大 |
| 退款扣减 | “各店铺的净销售额” | 忘记扣退款，或者没限定 `refund_status = '退款成功'` |
| 日期角色 | “3 月支付的订单有多少” | 用 `order_date_id` 代替 `pay_date_id` 关联日期 |
| 地区角色 | “发往上海的订单” vs “上海用户的订单” | 混用收货城市和常住城市 |
| 雪花关联 | “美妆个护的毛利” | 漏掉 `dim_sku → dim_category` 这一跳，或者毛利漏乘数量 |
| 去重计数 | “支付买家数” | 用 `COUNT(user_id)` 而不是 `COUNT(DISTINCT user_id)` |
| 活动归因 | “双 11 活动商品的销量” | 用日期范围代替 `activity_id`，或者反过来 |
| 相对时间 | “上个月”“去年同期” | “今天”算错，或者同比的时间窗口不对齐 |
| 漏斗分母 | “浏览转化率” | 分子分母的时间范围或人群不一致 |

## 七、数据生成方式

数据量较大，不再在 `dw.sql` 里手写 INSERT。改为：

- `docker/mysql/dw.sql` 只保留建表语句。
- 新增 `app/scripts/generate_dw_data.py`：用固定随机种子生成数据并写入 DW MySQL，**每次生成的结果完全一致**（评测的标准答案依赖这一点）。支持 `--scale small|medium`。
- 生成规则尽量贴近真实业务：
  - 618（6/1～6/18）、双 11（10/20～11/11）、年货节期间销量明显上升，周末略高；
  - 各品类价格带和件数分布不同：数码客单价高、件数少，食品客单价低、件数多；
  - 订单约 8% 取消、3% 未支付；退款率按品类区分，服饰约 15%，数码约 3%；
  - 用户分布偏向一线和新一线城市，会员等级越高复购越多；
  - 行为漏斗大致为 浏览 → 加购 → 下单 ≈ 100 : 12 : 4。

## 八、确认后需要改动的地方

项目的 Python 代码本身**和数仓结构无关**：实体（`ColumnInfo`、`TableInfo` 等）、ORM 模型和问数图描述的都是“元数据”，没有任何写死某张业务表的类。所以主要改动在数据和配置上：

| 改动 | 文件 |
| --- | --- |
| 新的建表语句 | `docker/mysql/dw.sql` |
| 数据生成脚本 | 新增 `app/scripts/generate_dw_data.py` |
| 表、字段、别名、取值同步开关、指标口径 | `conf/meta_config.yaml`（整体重写） |
| “今天”改为 2025-12-31 | `conf/app_config.yaml` 的 `agent.today` |
| 重新标定 ES 取值召回阈值 | `conf/app_config.yaml` 的 `es.min_score` |
| 示例问题 | 前端 `App.tsx`、`EmptyState`，`graph.py` 末尾的调试问题 |
| 文档 | README“示例数据”一节、`CLAUDE.md` |

**建议同时做一处代码改动：给外键加上显式的关联目标。** 目前模型只能根据字段名猜 join 条件，而新结构里 `receiver_region_id`、`pay_date_id` 这类字段名和维度表主键并不一致，靠猜很容易出错。建议在 `meta_config.yaml` 的外键字段上增加 `ref: dim_region.region_id`，并沿着下面这条链路一路带进 SQL 生成的 Prompt：

`ColumnConfig` → `ColumnInfo` 实体 → `column_info` 表（`meta.sql` 新增 `ref_column` 列）→ ORM 模型和 Mapper → `merge_retrieved_info` 输出的 `ColumnInfoState`

这是本次唯一需要改动的“表结构相关的类”。

## 九、待你确认的问题

1. **表的范围**：4 张事实表 + 7 张维度表是否合适？是否需要再加评价表 `fact_review`（评分、有无图片，用于“好评率”）？
2. **时间范围和数据量**：2024–2025 两年，约 6 万订单、20 万条行为，是否合适？
3. **是否采用 `ref` 外键方案**（第八节）？
4. **命名风格**：继续用 `dim_` / `fact_` 前缀，还是改成 OneData 风格（`dwd_trade_order_di` 这类）？前者对模型更友好，**推荐保留**。
