-- 教学数仓 dw：电商交易域星型模型（4 张事实表 + 7 张维度表）
-- 本文件只负责建表，数据由 app/scripts/generate_dw_data.py 用固定随机种子生成并写入
-- MySQL 容器首次初始化时会执行本文件；生成脚本也会读取其中的 DROP / CREATE TABLE 语句重建表
-- 表结构设计说明见 docs/design/dw-schema-v2.md

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS dw DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON dw.* TO 'didilili'@'%';
USE dw;

-- ---------------------------------------------------------------------------
-- 维度表
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date
(
    date_id      INT PRIMARY KEY COMMENT '日期键，格式 yyyyMMdd',
    full_date    DATE        NOT NULL COMMENT '日期',
    year         SMALLINT    NOT NULL COMMENT '年',
    quarter      TINYINT     NOT NULL COMMENT '季度 1-4',
    month        TINYINT     NOT NULL COMMENT '月',
    day          TINYINT     NOT NULL COMMENT '日',
    week_of_year TINYINT     NOT NULL COMMENT 'ISO 周序号',
    day_of_week  TINYINT     NOT NULL COMMENT '星期几，1=周一 7=周日',
    is_weekend   TINYINT(1)  NOT NULL COMMENT '是否周末',
    is_holiday   TINYINT(1)  NOT NULL COMMENT '是否法定节假日',
    holiday_name VARCHAR(20) NULL COMMENT '节假日名称'
) COMMENT '日期维度';

DROP TABLE IF EXISTS dim_region;
CREATE TABLE dim_region
(
    region_id VARCHAR(20) PRIMARY KEY COMMENT '城市编码',
    province  VARCHAR(20) NOT NULL COMMENT '省份',
    city      VARCHAR(20) NOT NULL COMMENT '城市',
    city_tier VARCHAR(10) NOT NULL COMMENT '城市等级：一线/新一线/二线/三线及以下',
    area      VARCHAR(10) NOT NULL COMMENT '大区：华东/华北/华南/华中/西南/西北/东北'
) COMMENT '地区维度（城市级）';

DROP TABLE IF EXISTS dim_user;
CREATE TABLE dim_user
(
    user_id          VARCHAR(20) PRIMARY KEY COMMENT '用户ID',
    user_name        VARCHAR(50) NOT NULL COMMENT '用户昵称',
    gender           VARCHAR(4)  NOT NULL COMMENT '性别：男/女/未知',
    age_range        VARCHAR(10) NOT NULL COMMENT '年龄段',
    region_id        VARCHAR(20) NOT NULL COMMENT '常住城市，关联 dim_region',
    member_level     VARCHAR(10) NOT NULL COMMENT '会员等级：普通会员/银卡/金卡/铂金/钻石',
    is_plus          TINYINT(1)  NOT NULL COMMENT '是否付费会员',
    register_date    DATE        NOT NULL COMMENT '注册日期',
    register_channel VARCHAR(10) NOT NULL COMMENT '注册渠道：APP/小程序/PC/H5',
    INDEX idx_region (region_id)
) COMMENT '用户维度';

DROP TABLE IF EXISTS dim_category;
CREATE TABLE dim_category
(
    category_id VARCHAR(20) PRIMARY KEY COMMENT '三级类目ID',
    category_l1 VARCHAR(20) NOT NULL COMMENT '一级类目',
    category_l2 VARCHAR(20) NOT NULL COMMENT '二级类目',
    category_l3 VARCHAR(20) NOT NULL COMMENT '三级类目'
) COMMENT '类目维度（三级类目）';

DROP TABLE IF EXISTS dim_shop;
CREATE TABLE dim_shop
(
    shop_id          VARCHAR(20) PRIMARY KEY COMMENT '店铺ID',
    shop_name        VARCHAR(50)  NOT NULL COMMENT '店铺名称',
    shop_type        VARCHAR(10)  NOT NULL COMMENT '店铺类型：自营/旗舰店/专营店/专卖店',
    main_category_l1 VARCHAR(20)  NOT NULL COMMENT '主营一级类目',
    shop_score       DECIMAL(3, 2) NOT NULL COMMENT '店铺综合评分',
    open_date        DATE         NOT NULL COMMENT '开店日期'
) COMMENT '店铺维度';

DROP TABLE IF EXISTS dim_sku;
CREATE TABLE dim_sku
(
    sku_id      VARCHAR(20) PRIMARY KEY COMMENT 'SKU ID',
    spu_id      VARCHAR(20)    NOT NULL COMMENT 'SPU ID，同款不同规格共享',
    sku_name    VARCHAR(100)   NOT NULL COMMENT '商品名称（含规格）',
    category_id VARCHAR(20)    NOT NULL COMMENT '三级类目，关联 dim_category',
    brand_name  VARCHAR(30)    NOT NULL COMMENT '品牌',
    shop_id     VARCHAR(20)    NOT NULL COMMENT '所属店铺，关联 dim_shop',
    list_price  DECIMAL(12, 2) NOT NULL COMMENT '标价',
    cost_price  DECIMAL(12, 2) NOT NULL COMMENT '成本价',
    launch_date DATE           NOT NULL COMMENT '上架日期',
    sku_status  VARCHAR(10)    NOT NULL COMMENT '状态：在售/下架',
    INDEX idx_category (category_id),
    INDEX idx_shop (shop_id)
) COMMENT '商品维度（SKU）';

DROP TABLE IF EXISTS dim_activity;
CREATE TABLE dim_activity
(
    activity_id   VARCHAR(20) PRIMARY KEY COMMENT '活动ID',
    activity_name VARCHAR(50) NOT NULL COMMENT '活动名称',
    activity_type VARCHAR(10) NOT NULL COMMENT '活动类型：大促/满减/直降/秒杀/优惠券',
    start_date    DATE        NOT NULL COMMENT '开始日期',
    end_date      DATE        NOT NULL COMMENT '结束日期'
) COMMENT '营销活动维度';

-- ---------------------------------------------------------------------------
-- 事实表
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS fact_order;
CREATE TABLE fact_order
(
    order_id           VARCHAR(30) PRIMARY KEY COMMENT '订单号',
    user_id            VARCHAR(20)    NOT NULL COMMENT '下单用户，关联 dim_user',
    shop_id            VARCHAR(20)    NOT NULL COMMENT '店铺，关联 dim_shop',
    receiver_region_id VARCHAR(20)    NOT NULL COMMENT '收货城市，关联 dim_region',
    order_date_id      INT            NOT NULL COMMENT '下单日期，关联 dim_date',
    order_time         DATETIME       NOT NULL COMMENT '下单时间',
    pay_date_id        INT            NULL COMMENT '支付日期，关联 dim_date，未支付为空',
    pay_time           DATETIME       NULL COMMENT '支付时间，未支付为空',
    platform           VARCHAR(10)    NOT NULL COMMENT '下单终端：APP/小程序/PC/H5',
    payment_method     VARCHAR(10)    NULL COMMENT '支付方式：支付宝/微信支付/银行卡/花呗分期',
    order_status       VARCHAR(10)    NOT NULL COMMENT '订单状态：待支付/已取消/已支付/已发货/已完成',
    item_quantity      INT            NOT NULL COMMENT '商品总件数',
    total_amount       DECIMAL(12, 2) NOT NULL COMMENT '商品总额',
    discount_amount    DECIMAL(12, 2) NOT NULL COMMENT '优惠总额',
    freight_amount     DECIMAL(12, 2) NOT NULL COMMENT '运费',
    order_amount       DECIMAL(12, 2) NOT NULL COMMENT '订单应付金额 = 商品总额 - 优惠总额 + 运费',
    INDEX idx_user (user_id),
    INDEX idx_shop (shop_id),
    INDEX idx_receiver_region (receiver_region_id),
    INDEX idx_order_date (order_date_id),
    INDEX idx_pay_date (pay_date_id)
) COMMENT '订单事实表（订单粒度）';

DROP TABLE IF EXISTS fact_order_item;
CREATE TABLE fact_order_item
(
    order_item_id   VARCHAR(30) PRIMARY KEY COMMENT '订单明细ID',
    order_id        VARCHAR(30)    NOT NULL COMMENT '所属订单，关联 fact_order',
    sku_id          VARCHAR(20)    NOT NULL COMMENT '商品，关联 dim_sku',
    activity_id     VARCHAR(20)    NULL COMMENT '参与的活动，关联 dim_activity，未参与为空',
    quantity        INT            NOT NULL COMMENT '购买件数',
    unit_price      DECIMAL(12, 2) NOT NULL COMMENT '成交单价',
    item_amount     DECIMAL(12, 2) NOT NULL COMMENT '商品金额 = 成交单价 × 件数',
    discount_amount DECIMAL(12, 2) NOT NULL COMMENT '分摊优惠',
    pay_amount      DECIMAL(12, 2) NOT NULL COMMENT '分摊实付金额 = 商品金额 - 分摊优惠，不含运费',
    INDEX idx_order (order_id),
    INDEX idx_sku (sku_id),
    INDEX idx_activity (activity_id)
) COMMENT '订单明细事实表（订单行粒度）';

DROP TABLE IF EXISTS fact_refund;
CREATE TABLE fact_refund
(
    refund_id       VARCHAR(30) PRIMARY KEY COMMENT '退款单号',
    order_item_id   VARCHAR(30)    NOT NULL COMMENT '退款的订单明细，关联 fact_order_item',
    order_id        VARCHAR(30)    NOT NULL COMMENT '所属订单，关联 fact_order',
    user_id         VARCHAR(20)    NOT NULL COMMENT '申请用户，关联 dim_user',
    apply_date_id   INT            NOT NULL COMMENT '申请日期，关联 dim_date',
    apply_time      DATETIME       NOT NULL COMMENT '申请时间',
    refund_type     VARCHAR(10)    NOT NULL COMMENT '退款类型：仅退款/退货退款',
    refund_reason   VARCHAR(20)    NOT NULL COMMENT '退款原因',
    refund_status   VARCHAR(10)    NOT NULL COMMENT '退款状态：退款中/退款成功/已拒绝/已撤销',
    refund_quantity INT            NOT NULL COMMENT '退款件数',
    refund_amount   DECIMAL(12, 2) NOT NULL COMMENT '退款金额',
    finish_time     DATETIME       NULL COMMENT '退款完成时间',
    INDEX idx_order_item (order_item_id),
    INDEX idx_order (order_id),
    INDEX idx_user (user_id),
    INDEX idx_apply_date (apply_date_id)
) COMMENT '退款事实表（退款申请粒度）';

DROP TABLE IF EXISTS fact_user_behavior;
CREATE TABLE fact_user_behavior
(
    behavior_id   BIGINT PRIMARY KEY COMMENT '行为ID',
    user_id       VARCHAR(20) NOT NULL COMMENT '用户，关联 dim_user',
    sku_id        VARCHAR(20) NOT NULL COMMENT '商品，关联 dim_sku',
    behavior_type VARCHAR(10) NOT NULL COMMENT '行为类型：浏览/收藏/加购',
    behavior_time DATETIME    NOT NULL COMMENT '行为时间',
    date_id       INT         NOT NULL COMMENT '行为日期，关联 dim_date',
    platform      VARCHAR(10) NOT NULL COMMENT '终端：APP/小程序/PC/H5',
    INDEX idx_user (user_id),
    INDEX idx_sku (sku_id),
    INDEX idx_date (date_id)
) COMMENT '用户行为事实表（行为粒度）';
