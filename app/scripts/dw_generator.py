"""
教学数仓数据生成器

按 docs/design/dw-schema-v2.md 的表结构，用固定随机种子生成 2024-01-01 ~ 2025-12-31 的电商交易数据
本模块只负责“生成”，返回 {表名: 行列表}，不连接数据库，也不读取项目配置，
因此可以直接在单元测试里调用；写入 MySQL 由 generate_dw_data.py 完成

数据里刻意保留了真实业务中的口径差异，用来检验问数 Agent 能否写出业务上正确的 SQL：
- 订单有“已取消”“待支付”，下单金额和支付金额不同
- 订单头和订单明细是两种粒度，金额在两张表上各有一份
- 退款单独成表，且只有“退款成功”才应扣减
- 下单日期和支付日期可能跨天，收货城市和用户常住城市可能不同
- 大促期间销量和优惠明显上升，各品类价格带 件数 退款率 毛利率不同

金额在内部统一用“分”做整数运算，保证订单头与明细的金额严格对平
"""

import bisect
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

START_DATE = date(2024, 1, 1)
END_DATE = date(2025, 12, 31)
END_TIME = datetime(2025, 12, 31, 23, 59, 59)
DEFAULT_SEED = 20240101


@dataclass(frozen=True)
class Scale:
    """数据规模：用户数 订单数 行为数，商品和店铺目录固定不随规模变化"""

    users: int
    orders: int
    behaviors: int


SCALES: dict[str, Scale] = {
    "small": Scale(users=600, orders=6_000, behaviors=20_000),
    "medium": Scale(users=3_000, orders=60_000, behaviors=200_000),
}

# ---------------------------------------------------------------------------
# 静态目录：地区 类目 品牌 活动 节假日
# ---------------------------------------------------------------------------

# (省份, 城市, 城市等级, 大区)，大区按华东/华北/华南/华中/西南/西北/东北传统划分
REGIONS: list[tuple[str, str, str, str]] = [
    ("北京市", "北京市", "一线", "华北"),
    ("上海市", "上海市", "一线", "华东"),
    ("广东省", "广州市", "一线", "华南"),
    ("广东省", "深圳市", "一线", "华南"),
    ("四川省", "成都市", "新一线", "西南"),
    ("浙江省", "杭州市", "新一线", "华东"),
    ("重庆市", "重庆市", "新一线", "西南"),
    ("湖北省", "武汉市", "新一线", "华中"),
    ("陕西省", "西安市", "新一线", "西北"),
    ("江苏省", "苏州市", "新一线", "华东"),
    ("天津市", "天津市", "新一线", "华北"),
    ("江苏省", "南京市", "新一线", "华东"),
    ("湖南省", "长沙市", "新一线", "华中"),
    ("河南省", "郑州市", "新一线", "华中"),
    ("广东省", "东莞市", "新一线", "华南"),
    ("山东省", "青岛市", "新一线", "华东"),
    ("辽宁省", "沈阳市", "新一线", "东北"),
    ("浙江省", "宁波市", "新一线", "华东"),
    ("云南省", "昆明市", "新一线", "西南"),
    ("安徽省", "合肥市", "二线", "华东"),
    ("广东省", "佛山市", "二线", "华南"),
    ("福建省", "福州市", "二线", "华东"),
    ("福建省", "厦门市", "二线", "华东"),
    ("山东省", "济南市", "二线", "华东"),
    ("黑龙江省", "哈尔滨市", "二线", "东北"),
    ("吉林省", "长春市", "二线", "东北"),
    ("广西壮族自治区", "南宁市", "二线", "华南"),
    ("河北省", "石家庄市", "二线", "华北"),
    ("贵州省", "贵阳市", "二线", "西南"),
    ("江西省", "南昌市", "二线", "华东"),
    ("山西省", "太原市", "二线", "华北"),
    ("辽宁省", "大连市", "二线", "东北"),
    ("江苏省", "无锡市", "二线", "华东"),
    ("浙江省", "温州市", "二线", "华东"),
    ("甘肃省", "兰州市", "三线及以下", "西北"),
    ("新疆维吾尔自治区", "乌鲁木齐市", "三线及以下", "西北"),
    ("内蒙古自治区", "呼和浩特市", "三线及以下", "华北"),
    ("海南省", "海口市", "三线及以下", "华南"),
    ("宁夏回族自治区", "银川市", "三线及以下", "西北"),
    ("青海省", "西宁市", "三线及以下", "西北"),
    ("河南省", "洛阳市", "三线及以下", "华中"),
    ("四川省", "绵阳市", "三线及以下", "西南"),
    ("江西省", "赣州市", "三线及以下", "华东"),
    ("广西壮族自治区", "桂林市", "三线及以下", "华南"),
]
# 用户常住地按城市等级分布，等级内平均
TIER_WEIGHTS = {"一线": 0.30, "新一线": 0.35, "二线": 0.22, "三线及以下": 0.13}


@dataclass(frozen=True)
class Leaf:
    """三级类目：价格带 产品线 规格"""

    l2: str
    l3: str
    price_lo: int
    price_hi: int
    lines: tuple[str, ...]
    specs: tuple[str, ...]


@dataclass(frozen=True)
class L1Profile:
    """一级类目的业务特征"""

    name: str
    order_share: float  # 订单量占比
    brands: tuple[str, ...]
    leaves: tuple[Leaf, ...]
    qty_weights: tuple[int, ...]  # 件数 1,2,3... 的权重
    max_items: int  # 一笔订单最多几个 SKU
    refund_rate: float  # 订单明细的退款申请概率
    return_goods_rate: float  # 退款中“退货退款”的占比
    cost_ratio: tuple[float, float]  # 成本价 / 标价
    refund_reasons: tuple[tuple[str, float], ...]


CATEGORIES: tuple[L1Profile, ...] = (
    L1Profile(
        name="手机数码",
        order_share=0.14,
        brands=("华为", "苹果", "小米", "荣耀", "OPPO", "vivo", "联想"),
        leaves=(
            Leaf(
                "手机通讯",
                "手机",
                1499,
                8999,
                ("旗舰手机", "影像手机", "轻薄手机"),
                ("256GB 黑色", "256GB 白色", "512GB 蓝色"),
            ),
            Leaf(
                "手机通讯",
                "手机壳膜",
                19,
                99,
                ("磁吸手机壳", "钢化膜"),
                ("透明", "黑色", "防窥"),
            ),
            Leaf(
                "电脑办公",
                "笔记本电脑",
                3999,
                12999,
                ("轻薄本", "游戏本"),
                ("16G+512G", "32G+1T"),
            ),
            Leaf(
                "电脑办公",
                "平板电脑",
                1999,
                6999,
                ("平板", "学习平板"),
                ("128GB", "256GB"),
            ),
            Leaf(
                "智能设备",
                "智能手表",
                599,
                3999,
                ("运动手表", "智能手表"),
                ("黑色", "银色"),
            ),
            Leaf(
                "智能设备", "耳机", 99, 1999, ("降噪耳机", "运动耳机"), ("白色", "黑色")
            ),
        ),
        qty_weights=(92, 7, 1),
        max_items=2,
        refund_rate=0.03,
        return_goods_rate=0.6,
        cost_ratio=(0.80, 0.88),
        refund_reasons=(
            ("质量问题", 0.35),
            ("不喜欢", 0.25),
            ("发错货", 0.1),
            ("物流太慢", 0.1),
            ("其他", 0.2),
        ),
    ),
    L1Profile(
        name="家用电器",
        order_share=0.12,
        brands=("美的", "格力", "海尔", "戴森", "小米", "苏泊尔", "九阳"),
        leaves=(
            Leaf(
                "大家电", "空调", 1999, 6999, ("变频空调", "新风空调"), ("1.5匹", "3匹")
            ),
            Leaf(
                "大家电",
                "冰箱",
                1599,
                8999,
                ("对开门冰箱", "十字门冰箱"),
                ("450L", "520L"),
            ),
            Leaf(
                "大家电",
                "洗衣机",
                999,
                4999,
                ("滚筒洗衣机", "洗烘一体机"),
                ("10公斤", "12公斤"),
            ),
            Leaf(
                "厨房小电", "电饭煲", 199, 999, ("IH电饭煲", "智能电饭煲"), ("3L", "4L")
            ),
            Leaf(
                "厨房小电",
                "空气炸锅",
                199,
                699,
                ("空气炸锅", "可视空气炸锅"),
                ("5L", "6L"),
            ),
            Leaf(
                "生活电器",
                "吸尘器",
                599,
                3999,
                ("无线吸尘器", "洗地机"),
                ("标准版", "旗舰版"),
            ),
            Leaf("生活电器", "电动牙刷", 99, 699, ("声波电动牙刷",), ("白色", "粉色")),
        ),
        qty_weights=(94, 5, 1),
        max_items=2,
        refund_rate=0.04,
        return_goods_rate=0.55,
        cost_ratio=(0.72, 0.82),
        refund_reasons=(
            ("质量问题", 0.4),
            ("不喜欢", 0.15),
            ("发错货", 0.1),
            ("物流太慢", 0.15),
            ("其他", 0.2),
        ),
    ),
    L1Profile(
        name="服饰鞋包",
        order_share=0.22,
        brands=("优衣库", "耐克", "阿迪达斯", "李宁", "安踏", "波司登", "太平鸟"),
        leaves=(
            Leaf(
                "女装", "连衣裙", 99, 599, ("法式连衣裙", "针织连衣裙"), ("S", "M", "L")
            ),
            Leaf(
                "女装",
                "羽绒服",
                299,
                1999,
                ("短款羽绒服", "长款羽绒服"),
                ("M", "L", "XL"),
            ),
            Leaf("男装", "T恤", 49, 299, ("纯棉T恤", "速干T恤"), ("M", "L", "XL")),
            Leaf("男装", "夹克", 199, 999, ("休闲夹克", "冲锋衣"), ("L", "XL", "XXL")),
            Leaf("鞋靴", "运动鞋", 299, 1299, ("跑步鞋", "篮球鞋"), ("40", "42", "44")),
            Leaf("鞋靴", "休闲鞋", 199, 799, ("板鞋", "帆布鞋"), ("38", "40", "42")),
            Leaf(
                "箱包", "双肩包", 99, 699, ("通勤双肩包", "运动背包"), ("黑色", "灰色")
            ),
        ),
        qty_weights=(70, 22, 8),
        max_items=3,
        refund_rate=0.15,
        return_goods_rate=0.85,
        cost_ratio=(0.35, 0.55),
        refund_reasons=(
            ("尺码不合适", 0.4),
            ("不喜欢", 0.25),
            ("质量问题", 0.15),
            ("发错货", 0.05),
            ("物流太慢", 0.05),
            ("其他", 0.1),
        ),
    ),
    L1Profile(
        name="美妆个护",
        order_share=0.16,
        brands=("欧莱雅", "雅诗兰黛", "兰蔻", "珀莱雅", "完美日记", "资生堂", "海飞丝"),
        leaves=(
            Leaf("护肤", "面霜", 99, 1299, ("保湿面霜", "抗皱面霜"), ("30ml", "50ml")),
            Leaf("护肤", "精华", 199, 1599, ("修护精华", "美白精华"), ("30ml", "50ml")),
            Leaf(
                "护肤", "面膜", 59, 299, ("补水面膜", "清洁泥膜"), ("10片装", "20片装")
            ),
            Leaf(
                "彩妆",
                "口红",
                99,
                399,
                ("丝绒口红", "滋润口红"),
                ("#999", "#888", "#520"),
            ),
            Leaf("彩妆", "粉底液", 149, 599, ("持妆粉底液",), ("自然色", "象牙白")),
            Leaf(
                "个人护理",
                "洗发水",
                39,
                149,
                ("去屑洗发水", "控油洗发水"),
                ("400ml", "750ml"),
            ),
        ),
        qty_weights=(68, 24, 8),
        max_items=3,
        refund_rate=0.06,
        return_goods_rate=0.5,
        cost_ratio=(0.30, 0.50),
        refund_reasons=(
            ("不喜欢", 0.3),
            ("质量问题", 0.25),
            ("发错货", 0.1),
            ("物流太慢", 0.15),
            ("其他", 0.2),
        ),
    ),
    L1Profile(
        name="食品饮料",
        order_share=0.16,
        brands=("三只松鼠", "良品铺子", "雀巢", "蒙牛", "伊利", "金龙鱼", "奥利奥"),
        leaves=(
            Leaf("休闲食品", "坚果", 29, 129, ("每日坚果", "碧根果"), ("500g", "1kg")),
            Leaf(
                "休闲食品", "饼干糕点", 15, 69, ("夹心饼干", "蛋黄酥"), ("300g", "600g")
            ),
            Leaf(
                "饮料冲调", "咖啡", 39, 199, ("速溶咖啡", "挂耳咖啡"), ("30条", "60条")
            ),
            Leaf(
                "饮料冲调",
                "牛奶",
                49,
                129,
                ("纯牛奶", "高钙牛奶"),
                ("250ml*12", "250ml*24"),
            ),
            Leaf("粮油调味", "大米", 39, 99, ("东北大米", "五常大米"), ("5kg", "10kg")),
        ),
        qty_weights=(45, 25, 15, 8, 4, 3),
        max_items=3,
        refund_rate=0.02,
        return_goods_rate=0.2,
        cost_ratio=(0.60, 0.75),
        refund_reasons=(
            ("质量问题", 0.45),
            ("发错货", 0.15),
            ("物流太慢", 0.2),
            ("其他", 0.2),
        ),
    ),
    L1Profile(
        name="母婴",
        order_share=0.08,
        brands=("飞鹤", "惠氏", "帮宝适", "花王", "巴拉巴拉", "乐高", "好孩子"),
        leaves=(
            Leaf(
                "奶粉",
                "婴幼儿奶粉",
                199,
                499,
                ("婴幼儿配方奶粉",),
                ("1段 800g", "2段 800g", "3段 800g"),
            ),
            Leaf(
                "尿裤湿巾",
                "纸尿裤",
                89,
                229,
                ("纸尿裤", "拉拉裤"),
                ("M码", "L码", "XL码"),
            ),
            Leaf(
                "童装玩具",
                "童装",
                59,
                299,
                ("儿童卫衣", "儿童外套"),
                ("110", "120", "130"),
            ),
            Leaf(
                "童装玩具", "益智玩具", 49, 399, ("积木", "拼图"), ("标准款", "豪华款")
            ),
        ),
        qty_weights=(55, 28, 10, 7),
        max_items=3,
        refund_rate=0.05,
        return_goods_rate=0.6,
        cost_ratio=(0.55, 0.70),
        refund_reasons=(
            ("质量问题", 0.3),
            ("尺码不合适", 0.2),
            ("不喜欢", 0.2),
            ("物流太慢", 0.1),
            ("其他", 0.2),
        ),
    ),
    L1Profile(
        name="家居日用",
        order_share=0.12,
        brands=("罗莱", "水星家纺", "蓝月亮", "维达", "清风", "太力", "富安娜"),
        leaves=(
            Leaf(
                "家纺",
                "四件套",
                129,
                799,
                ("纯棉四件套", "天丝四件套"),
                ("1.5m床", "1.8m床"),
            ),
            Leaf("家纺", "枕头", 49, 299, ("乳胶枕", "记忆棉枕"), ("单只装", "两只装")),
            Leaf("清洁用品", "洗衣液", 29, 99, ("洗衣液", "洗衣凝珠"), ("2kg", "4kg")),
            Leaf("清洁用品", "纸巾", 29, 79, ("抽纸", "卷纸"), ("24包", "36包")),
            Leaf(
                "收纳",
                "收纳箱",
                29,
                149,
                ("折叠收纳箱", "衣物收纳箱"),
                ("小号", "大号"),
            ),
        ),
        qty_weights=(55, 25, 12, 8),
        max_items=3,
        refund_rate=0.06,
        return_goods_rate=0.55,
        cost_ratio=(0.50, 0.65),
        refund_reasons=(
            ("质量问题", 0.3),
            ("不喜欢", 0.25),
            ("发错货", 0.1),
            ("物流太慢", 0.15),
            ("其他", 0.2),
        ),
    ),
)

# (名称, 类型, 开始, 结束)；大促期间订单量和优惠力度明显上升
ACTIVITIES: list[tuple[str, str, date, date]] = [
    ("2024年货节", "大促", date(2024, 1, 15), date(2024, 2, 5)),
    ("2024年38女王节", "大促", date(2024, 3, 1), date(2024, 3, 8)),
    ("2024年618大促", "大促", date(2024, 6, 1), date(2024, 6, 18)),
    ("2024年双11", "大促", date(2024, 10, 20), date(2024, 11, 11)),
    ("2024年双12", "大促", date(2024, 12, 1), date(2024, 12, 12)),
    ("2025年货节", "大促", date(2025, 1, 5), date(2025, 1, 26)),
    ("2025年38女王节", "大促", date(2025, 3, 1), date(2025, 3, 8)),
    ("2025年618大促", "大促", date(2025, 6, 1), date(2025, 6, 18)),
    ("2025年双11", "大促", date(2025, 10, 20), date(2025, 11, 11)),
    ("2025年双12", "大促", date(2025, 12, 1), date(2025, 12, 12)),
    ("日常满199减20", "满减", START_DATE, END_DATE),
    ("品牌秒杀日", "秒杀", START_DATE, END_DATE),
    ("新人专享券", "优惠券", START_DATE, END_DATE),
    ("限时直降", "直降", START_DATE, END_DATE),
]
# 大促的订单量倍数；当天另有峰值倍数
PROMO_UPLIFT = {
    "年货节": 1.4,
    "38女王节": 1.5,
    "618大促": 1.9,
    "双11": 2.0,
    "双12": 1.5,
}
PEAK_DAYS = {
    (6, 1): 2.0,
    (6, 18): 4.0,
    (10, 31): 2.5,
    (11, 11): 6.0,
    (12, 12): 2.5,
    (3, 8): 1.8,
}
MONTH_SEASONALITY = {
    1: 1.0,
    2: 0.85,
    3: 0.95,
    4: 0.95,
    5: 1.0,
    6: 1.1,
    7: 0.9,
    8: 0.9,
    9: 1.0,
    10: 1.05,
    11: 1.2,
    12: 1.1,
}
YEAR_GROWTH = {2024: 1.0, 2025: 1.15}

# 法定节假日：(名称, 开始, 结束)
HOLIDAYS: list[tuple[str, date, date]] = [
    ("元旦", date(2024, 1, 1), date(2024, 1, 1)),
    ("春节", date(2024, 2, 10), date(2024, 2, 17)),
    ("清明节", date(2024, 4, 4), date(2024, 4, 6)),
    ("劳动节", date(2024, 5, 1), date(2024, 5, 5)),
    ("端午节", date(2024, 6, 8), date(2024, 6, 10)),
    ("中秋节", date(2024, 9, 15), date(2024, 9, 17)),
    ("国庆节", date(2024, 10, 1), date(2024, 10, 7)),
    ("元旦", date(2025, 1, 1), date(2025, 1, 1)),
    ("春节", date(2025, 1, 28), date(2025, 2, 4)),
    ("清明节", date(2025, 4, 4), date(2025, 4, 6)),
    ("劳动节", date(2025, 5, 1), date(2025, 5, 5)),
    ("端午节", date(2025, 5, 31), date(2025, 6, 2)),
    ("国庆节", date(2025, 10, 1), date(2025, 10, 5)),
    ("中秋节", date(2025, 10, 6), date(2025, 10, 8)),
]
SPRING_FESTIVALS = [
    (date(2024, 2, 10), date(2024, 2, 17)),
    (date(2025, 1, 28), date(2025, 2, 4)),
]

MEMBER_LEVELS = [
    ("普通会员", 0.45, 1.0, 0.03),
    ("银卡", 0.25, 1.5, 0.08),
    ("金卡", 0.17, 2.5, 0.20),
    ("铂金", 0.09, 4.0, 0.40),
    ("钻石", 0.04, 6.0, 0.65),
]
AGE_RANGES = [
    ("18-24", 0.18),
    ("25-29", 0.24),
    ("30-34", 0.22),
    ("35-39", 0.15),
    ("40-49", 0.14),
    ("50及以上", 0.07),
]
GENDERS = [("女", 0.55), ("男", 0.42), ("未知", 0.03)]
PLATFORMS = [("APP", 0.60), ("小程序", 0.22), ("PC", 0.10), ("H5", 0.08)]
PAYMENT_METHODS = [
    ("支付宝", 0.45),
    ("微信支付", 0.38),
    ("银行卡", 0.07),
    ("花呗分期", 0.10),
]
# 一天中各小时的下单权重：上午 午后和晚间三个高峰
HOUR_WEIGHTS = [
    3,
    1.5,
    0.8,
    0.5,
    0.4,
    0.5,
    1,
    2,
    3.5,
    5,
    6,
    5.5,
    5,
    5.5,
    6,
    6,
    5.5,
    5,
    5.5,
    7,
    8.5,
    9,
    8,
    5.5,
]

NICK_PREFIX = [
    "快乐的",
    "爱吃",
    "追风的",
    "安静的",
    "小",
    "阿",
    "认真的",
    "温柔的",
    "爱旅行的",
    "早起的",
]
NICK_NOUN = [
    "小熊",
    "橘子",
    "星星",
    "鲸鱼",
    "兔子",
    "柠檬",
    "海盐",
    "松鼠",
    "芒果",
    "云朵",
    "布丁",
    "可乐",
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _money(cents: int) -> Decimal:
    """把“分”转换成两位小数的 Decimal，写入 DECIMAL 字段不丢精度"""
    return Decimal(cents).scaleb(-2)


def _pick(rng: random.Random, options: list[tuple[Any, float]]) -> Any:
    """按权重从 (值, 权重) 列表里选一个值"""
    values = [o[0] for o in options]
    weights = [o[1] for o in options]
    return rng.choices(values, weights=weights, k=1)[0]


def _strip_private(rows: list[dict]) -> list[dict]:
    """去掉以下划线开头的内部辅助字段，只保留要写入数据库的列"""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def _date_id(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _nice_price(rng: random.Random, lo: int, hi: int) -> int:
    """生成以 9 结尾的标价（单位：元），更像真实商品定价"""
    price = rng.uniform(lo, hi)
    if price >= 1000:
        return int(price // 100) * 100 + 99
    if price >= 100:
        return int(price // 10) * 10 + 9
    return int(price // 10) * 10 + 9


def _promo_of(d: date) -> tuple[int, str] | None:
    """返回当天所在大促的 (活动序号, 活动名)，不在大促期间返回 None"""
    for idx, (name, kind, start, end) in enumerate(ACTIVITIES):
        if kind == "大促" and start <= d <= end:
            return idx, name
    return None


# ---------------------------------------------------------------------------
# 维度表
# ---------------------------------------------------------------------------


def _gen_dim_date() -> list[dict]:
    holiday_of: dict[date, str] = {}
    for name, start, end in HOLIDAYS:
        for d in _days(start, end):
            holiday_of[d] = name
    rows = []
    for d in _days(START_DATE, END_DATE):
        iso = d.isocalendar()
        rows.append(
            {
                "date_id": _date_id(d),
                "full_date": d,
                "year": d.year,
                "quarter": (d.month - 1) // 3 + 1,
                "month": d.month,
                "day": d.day,
                "week_of_year": iso.week,
                "day_of_week": iso.weekday,
                "is_weekend": 1 if iso.weekday >= 6 else 0,
                "is_holiday": 1 if d in holiday_of else 0,
                "holiday_name": holiday_of.get(d),
            }
        )
    return rows


def _gen_dim_region() -> list[dict]:
    return [
        {
            "region_id": f"R{i + 1:03d}",
            "province": province,
            "city": city,
            "city_tier": tier,
            "area": area,
        }
        for i, (province, city, tier, area) in enumerate(REGIONS)
    ]


def _gen_dim_category() -> tuple[list[dict], dict[str, tuple[L1Profile, Leaf]]]:
    rows = []
    leaf_of: dict[str, tuple[L1Profile, Leaf]] = {}
    for i, profile in enumerate(CATEGORIES):
        l2_names = list(dict.fromkeys(leaf.l2 for leaf in profile.leaves))
        l3_seq: dict[str, int] = {}
        for leaf in profile.leaves:
            j = l2_names.index(leaf.l2)
            l3_seq[leaf.l2] = l3_seq.get(leaf.l2, 0) + 1
            category_id = f"C{i + 1:02d}{j + 1:02d}{l3_seq[leaf.l2]:02d}"
            rows.append(
                {
                    "category_id": category_id,
                    "category_l1": profile.name,
                    "category_l2": leaf.l2,
                    "category_l3": leaf.l3,
                }
            )
            leaf_of[category_id] = (profile, leaf)
    return rows, leaf_of


def _gen_dim_shop(rng: random.Random) -> list[dict]:
    """每个一级类目：1 家自营 + 4 家品牌旗舰店 + 1 家专卖店 + 1 家专营店"""
    rows = []
    city_names = [
        city.removesuffix("市") for _, city, tier, _ in REGIONS if tier != "三线及以下"
    ]
    for i, profile in enumerate(CATEGORIES):
        brands = profile.brands
        shops = [("自营", f"商城自营{profile.name}馆", brands)]
        shops += [("旗舰店", f"{b}官方旗舰店", (b,)) for b in brands[:4]]
        shops.append(
            ("专卖店", f"{brands[4]}{rng.choice(city_names)}专卖店", (brands[4],))
        )
        shops.append(
            ("专营店", f"{rng.choice(city_names)}{profile.name}专营店", brands[4:])
        )
        for j, (shop_type, shop_name, _) in enumerate(shops):
            rows.append(
                {
                    "shop_id": f"S{i + 1:02d}{j + 1:02d}",
                    "shop_name": shop_name,
                    "shop_type": shop_type,
                    "main_category_l1": profile.name,
                    # 自营和旗舰店评分整体更高
                    "shop_score": _money(
                        rng.randint(475, 498)
                        if shop_type in ("自营", "旗舰店")
                        else rng.randint(440, 485)
                    ),
                    "open_date": date(2015, 1, 1)
                    + timedelta(days=rng.randint(0, 365 * 8)),
                    "_brands": shops[j][2],
                    "_l1": profile.name,
                }
            )
    return rows


def _gen_dim_sku(
    rng: random.Random,
    shops: list[dict],
    categories: list[dict],
    leaf_of: dict[str, tuple[L1Profile, Leaf]],
) -> list[dict]:
    """每家店铺 3~5 个 SPU，每个 SPU 按规格拆成 1~3 个 SKU"""
    leaves_by_l1: dict[str, list[tuple[str, Leaf]]] = {}
    for cat in categories:
        profile, leaf = leaf_of[cat["category_id"]]
        leaves_by_l1.setdefault(profile.name, []).append((cat["category_id"], leaf))

    profile_of = {p.name: p for p in CATEGORIES}
    rows = []
    spu_seq = 0
    for shop in shops:
        profile = profile_of[shop["_l1"]]
        leaves = leaves_by_l1[profile.name]
        for _ in range(rng.randint(3, 5)):
            spu_seq += 1
            category_id, leaf = rng.choice(leaves)
            brand = rng.choice(shop["_brands"])
            line = rng.choice(leaf.lines)
            base_price = _nice_price(rng, leaf.price_lo, leaf.price_hi)
            launch = date(2022, 1, 1) + timedelta(days=rng.randint(0, 1370))
            n_specs = rng.randint(1, min(3, len(leaf.specs)))
            for k, spec in enumerate(rng.sample(leaf.specs, n_specs)):
                # 规格越高价格越高，例如 512GB 比 256GB 贵
                price = _nice_price(
                    rng,
                    int(base_price * (1 + 0.1 * k)),
                    int(base_price * (1 + 0.1 * k)) + 1,
                )
                cost = int(price * 100 * rng.uniform(*profile.cost_ratio))
                rows.append(
                    {
                        "sku_id": f"K{len(rows) + 1:05d}",
                        "spu_id": f"P{spu_seq:04d}",
                        "sku_name": f"{brand} {line} {spec}",
                        "category_id": category_id,
                        "brand_name": brand,
                        "shop_id": shop["shop_id"],
                        "list_price": _money(price * 100),
                        "cost_price": _money(cost),
                        "launch_date": launch,
                        "sku_status": "下架" if rng.random() < 0.08 else "在售",
                    }
                )
    return rows


def _gen_dim_user(rng: random.Random, n: int, regions: list[dict]) -> list[dict]:
    region_weights = []
    tier_count: dict[str, int] = {}
    for r in regions:
        tier_count[r["city_tier"]] = tier_count.get(r["city_tier"], 0) + 1
    for r in regions:
        region_weights.append(
            (r["region_id"], TIER_WEIGHTS[r["city_tier"]] / tier_count[r["city_tier"]])
        )

    rows = []
    for i in range(n):
        level, _, activity, plus_rate = _pick(rng, [(m, m[1]) for m in MEMBER_LEVELS])
        # 55% 是 2024 年之前的老用户，其余在数据期内陆续注册
        if rng.random() < 0.55:
            register = date(2018, 1, 1) + timedelta(
                days=rng.randint(0, (date(2023, 12, 31) - date(2018, 1, 1)).days)
            )
        else:
            register = START_DATE + timedelta(
                days=rng.randint(0, (date(2025, 11, 30) - START_DATE).days)
            )
        rows.append(
            {
                "user_id": f"U{i + 1:06d}",
                "user_name": f"{rng.choice(NICK_PREFIX)}{rng.choice(NICK_NOUN)}{rng.randint(10, 9999)}",
                "gender": _pick(rng, GENDERS),
                "age_range": _pick(rng, AGE_RANGES),
                "region_id": _pick(rng, region_weights),
                "member_level": level,
                "is_plus": 1 if rng.random() < plus_rate else 0,
                "register_date": register,
                "register_channel": _pick(rng, PLATFORMS),
                "_activity": activity,
            }
        )
    return rows


def _gen_dim_activity() -> list[dict]:
    return [
        {
            "activity_id": f"A{i + 1:02d}",
            "activity_name": name,
            "activity_type": kind,
            "start_date": start,
            "end_date": end,
        }
        for i, (name, kind, start, end) in enumerate(ACTIVITIES)
    ]


# ---------------------------------------------------------------------------
# 事实表
# ---------------------------------------------------------------------------


def _day_weight(d: date) -> float:
    """某一天的相对订单量：月份季节性 × 年增长 × 周末 × 大促 × 春节"""
    w = MONTH_SEASONALITY[d.month] * YEAR_GROWTH[d.year]
    if d.weekday() >= 5:
        w *= 1.15
    promo = _promo_of(d)
    if promo:
        for key, uplift in PROMO_UPLIFT.items():
            if key in promo[1]:
                w *= uplift
        w *= PEAK_DAYS.get((d.month, d.day), 1.0)
    for start, end in SPRING_FESTIVALS:
        if start <= d <= end:
            w *= 0.6
    return w


def _category_affinity(user: dict) -> list[tuple[L1Profile, float]]:
    """用户对各一级类目的偏好：女性偏美妆服饰，25~39 岁偏母婴"""
    result = []
    for p in CATEGORIES:
        w = p.order_share
        if user["gender"] == "女" and p.name in ("美妆个护", "服饰鞋包"):
            w *= 1.5
        if user["gender"] == "男" and p.name in ("手机数码", "家用电器"):
            w *= 1.5
        if p.name == "母婴":
            w *= 2.2 if user["age_range"] in ("25-29", "30-34", "35-39") else 0.3
        result.append((p, w))
    return result


def generate(scale: str = "medium", seed: int = DEFAULT_SEED) -> dict[str, list[dict]]:
    """生成全部 11 张表的数据，返回 {表名: 行列表}，表的顺序即写入顺序"""

    cfg = SCALES[scale]
    rng = random.Random(seed)

    dim_date = _gen_dim_date()
    dim_region = _gen_dim_region()
    dim_category, leaf_of = _gen_dim_category()
    dim_shop = _gen_dim_shop(rng)
    dim_sku = _gen_dim_sku(rng, dim_shop, dim_category, leaf_of)
    dim_user = _gen_dim_user(rng, cfg.users, dim_region)
    dim_activity = _gen_dim_activity()

    profile_of_sku = {s["sku_id"]: leaf_of[s["category_id"]][0] for s in dim_sku}
    shop_type_of = {s["shop_id"]: s["shop_type"] for s in dim_shop}
    skus_by_shop: dict[str, list[dict]] = {}
    for s in dim_sku:
        skus_by_shop.setdefault(s["shop_id"], []).append(s)
    shops_by_l1: dict[str, list[tuple[str, float]]] = {}
    for s in dim_shop:
        weight = {"自营": 3.0, "旗舰店": 1.2, "专卖店": 0.6, "专营店": 0.8}[
            s["shop_type"]
        ]
        shops_by_l1.setdefault(s["_l1"], []).append((s["shop_id"], weight))
    activity_id_of = {a["activity_name"]: a["activity_id"] for a in dim_activity}
    region_ids = [r["region_id"] for r in dim_region]

    # 用户按注册日期排序，某天可下单的用户就是一个前缀，用前缀和做加权抽样
    users_sorted = sorted(dim_user, key=lambda u: (u["register_date"], u["user_id"]))
    reg_dates = [u["register_date"] for u in users_sorted]
    cum_weights: list[float] = []
    acc = 0.0
    for u in users_sorted:
        acc += u["_activity"] * (1.5 if u["is_plus"] else 1.0)
        cum_weights.append(acc)
    affinity_of = {u["user_id"]: _category_affinity(u) for u in dim_user}
    first_order_done: set[str] = set()

    # 按日权重把订单总数分配到每一天
    all_days = _days(START_DATE, END_DATE)
    day_weights = [_day_weight(d) for d in all_days]
    total_w = sum(day_weights)
    orders_per_day = []
    for w in day_weights:
        expected = cfg.orders * w / total_w
        n = int(expected)
        if rng.random() < expected - n:
            n += 1
        orders_per_day.append(n)

    fact_order: list[dict] = []
    fact_order_item: list[dict] = []
    order_meta: list[tuple[dict, list[tuple[dict, L1Profile, bool]]]] = []

    for d, n_orders in zip(all_days, orders_per_day):
        n_eligible = bisect.bisect_right(reg_dates, d)
        if n_eligible == 0:
            continue
        promo = _promo_of(d)
        times = sorted(
            datetime(
                d.year,
                d.month,
                d.day,
                rng.choices(range(24), HOUR_WEIGHTS)[0],
                rng.randint(0, 59),
                rng.randint(0, 59),
            )
            for _ in range(n_orders)
        )
        for seq, order_time in enumerate(times, start=1):
            total_eligible = cum_weights[n_eligible - 1]
            user = users_sorted[
                bisect.bisect_left(cum_weights, rng.random() * total_eligible)
            ]
            profile = _pick(rng, affinity_of[user["user_id"]])
            shop_id = _pick(rng, shops_by_l1[profile.name])
            # 只能买到当天已上架、且没有在 2025 下半年下架的商品
            available = [
                s
                for s in skus_by_shop[shop_id]
                if s["launch_date"] <= d
                and not (s["sku_status"] == "下架" and d > date(2025, 6, 30))
            ]
            if not available:
                continue
            n_items = min(
                len(available),
                rng.choices(
                    range(1, profile.max_items + 1), [60, 30, 10][: profile.max_items]
                )[0],
            )
            is_first = user["user_id"] not in first_order_done

            order_id = f"O{_date_id(d)}{seq:05d}"
            items = []
            for k, sku in enumerate(rng.sample(available, n_items), start=1):
                qty = rng.choices(
                    range(1, len(profile.qty_weights) + 1), profile.qty_weights
                )[0]
                list_cents = int(sku["list_price"] * 100)
                activity_name = None
                price_factor = 1.0
                r = rng.random()
                if promo and r < 0.75:
                    activity_name = promo[1]
                    price_factor = rng.uniform(0.80, 0.92)
                elif is_first and r < 0.8:
                    activity_name = "新人专享券"
                elif r < 0.12:
                    activity_name = "日常满199减20"
                elif r < 0.16:
                    activity_name = "品牌秒杀日"
                    price_factor = rng.uniform(0.75, 0.85)
                elif r < 0.24:
                    activity_name = "限时直降"
                    price_factor = rng.uniform(0.88, 0.95)
                unit_cents = int(round(list_cents * price_factor))
                items.append(
                    {
                        "order_item_id": f"{order_id}-{k}",
                        "order_id": order_id,
                        "sku_id": sku["sku_id"],
                        "activity_id": activity_id_of[activity_name]
                        if activity_name
                        else None,
                        "quantity": qty,
                        "_unit": unit_cents,
                        "_amount": unit_cents * qty,
                        "_activity": activity_name,
                    }
                )

            # 订单级优惠：大促跨店满 300 减 50、日常满 199 减 20、新人券 10 元，再按金额分摊到明细
            total_cents = sum(i["_amount"] for i in items)
            activities = {i["_activity"] for i in items}
            discount = 0
            if promo and promo[1] in activities:
                discount += (total_cents // 30000) * 5000
            if "日常满199减20" in activities:
                discount += (total_cents // 19900) * 2000
            if "新人专享券" in activities and total_cents >= 2900:
                discount += 1000
            if rng.random() < 0.05:
                discount += rng.choice([500, 1000, 2000])  # 平台随机券
            discount = min(discount, total_cents // 2)
            allocated = 0
            for idx, item in enumerate(items):
                if idx == len(items) - 1:
                    share = discount - allocated
                else:
                    share = discount * item["_amount"] // total_cents
                allocated += share
                item["_discount"] = share

            payable = total_cents - discount
            if shop_type_of[shop_id] == "自营":
                freight = 0 if payable >= 5900 else 600
            else:
                freight = (
                    0
                    if payable >= 9900 or rng.random() < 0.6
                    else rng.choice([800, 1000])
                )

            # 订单状态：约 8% 未支付取消；年末最后一晚的部分订单仍待支付
            pay_time = None
            if rng.random() < 0.08:
                status = "已取消"
            elif d == END_DATE and order_time.hour >= 22 and rng.random() < 0.4:
                status = "待支付"
            else:
                pay_time = order_time + timedelta(
                    seconds=int(min(rng.expovariate(1 / 120), 1800)) + 5
                )
                if pay_time > END_TIME:
                    pay_time, status = None, "待支付"
                else:
                    days_left = (END_DATE - pay_time.date()).days
                    status = (
                        "已完成"
                        if days_left >= 10
                        else ("已发货" if days_left >= 2 else "已支付")
                    )
            payment_method = None
            if pay_time:
                methods = list(PAYMENT_METHODS)
                if payable >= 200000:
                    methods = [
                        ("支付宝", 0.38),
                        ("微信支付", 0.30),
                        ("银行卡", 0.07),
                        ("花呗分期", 0.25),
                    ]
                payment_method = _pick(rng, methods)
                first_order_done.add(user["user_id"])

            order = {
                "order_id": order_id,
                "user_id": user["user_id"],
                "shop_id": shop_id,
                # 约 15% 的订单寄往常住地以外的城市（送礼 出差等）
                "receiver_region_id": user["region_id"]
                if rng.random() < 0.85
                else rng.choice(region_ids),
                "order_date_id": _date_id(d),
                "order_time": order_time,
                "pay_date_id": _date_id(pay_time.date()) if pay_time else None,
                "pay_time": pay_time,
                # 大多数订单来自用户的注册渠道
                "platform": user["register_channel"]
                if rng.random() < 0.6
                else _pick(rng, PLATFORMS),
                "payment_method": payment_method,
                "order_status": status,
                "item_quantity": sum(i["quantity"] for i in items),
                "total_amount": _money(total_cents),
                "discount_amount": _money(discount),
                "freight_amount": _money(freight),
                "order_amount": _money(payable + freight),
            }
            fact_order.append(order)
            item_meta = []
            for item in items:
                pay_cents = item["_amount"] - item["_discount"]
                row = {
                    "order_item_id": item["order_item_id"],
                    "order_id": order_id,
                    "sku_id": item["sku_id"],
                    "activity_id": item["activity_id"],
                    "quantity": item["quantity"],
                    "unit_price": _money(item["_unit"]),
                    "item_amount": _money(item["_amount"]),
                    "discount_amount": _money(item["_discount"]),
                    "pay_amount": _money(pay_cents),
                }
                fact_order_item.append(row)
                item_meta.append(
                    (
                        row,
                        profile_of_sku[item["sku_id"]],
                        item["_activity"] is not None
                        and promo is not None
                        and item["_activity"] == promo[1],
                    )
                )
            order_meta.append((order, item_meta))

    fact_refund = _gen_fact_refund(rng, order_meta)
    fact_user_behavior = _gen_fact_user_behavior(
        rng, cfg.behaviors, order_meta, users_sorted, reg_dates, dim_sku
    )

    return {
        "dim_date": dim_date,
        "dim_region": dim_region,
        "dim_category": dim_category,
        "dim_shop": _strip_private(dim_shop),
        "dim_sku": dim_sku,
        "dim_user": _strip_private(dim_user),
        "dim_activity": dim_activity,
        "fact_order": fact_order,
        "fact_order_item": fact_order_item,
        "fact_refund": fact_refund,
        "fact_user_behavior": fact_user_behavior,
    }


def _gen_fact_refund(rng: random.Random, order_meta: list) -> list[dict]:
    """对已支付订单的明细按品类退款率生成退款申请，大促商品退款率更高"""
    refunds = []
    for order, items in order_meta:
        if order["pay_time"] is None:
            continue
        for row, profile, in_promo in items:
            rate = profile.refund_rate * (1.3 if in_promo else 1.0)
            if rng.random() >= rate:
                continue
            return_goods = rng.random() < profile.return_goods_rate
            # 仅退款多发生在发货前后两三天内，退货退款要等收货之后
            delay = (
                timedelta(hours=rng.uniform(1, 72))
                if not return_goods
                else timedelta(days=rng.uniform(3, 15))
            )
            apply_time = (order["pay_time"] + delay).replace(microsecond=0)
            if apply_time > END_TIME:
                continue
            qty = row["quantity"]
            refund_qty = (
                qty if qty == 1 or rng.random() < 0.6 else rng.randint(1, qty - 1)
            )
            pay_cents = int(row["pay_amount"] * 100)
            amount = pay_cents * refund_qty // qty
            reason = _pick(rng, list(profile.refund_reasons))
            if not return_goods and reason == "质量问题" and rng.random() < 0.3:
                amount = int(amount * rng.uniform(0.2, 0.5))  # 协商部分退款
            days_left = (END_TIME - apply_time).days
            if days_left < 7 and rng.random() < 0.5:
                status = "退款中"
            else:
                status = _pick(
                    rng, [("退款成功", 0.80), ("已拒绝", 0.08), ("已撤销", 0.12)]
                )
            finish_time = None
            if status in ("退款成功", "已拒绝"):
                finish_time = apply_time + timedelta(hours=rng.uniform(2, 120))
            elif status == "已撤销":
                finish_time = apply_time + timedelta(hours=rng.uniform(1, 48))
            if finish_time and finish_time > END_TIME:
                status, finish_time = "退款中", None
            refunds.append(
                {
                    "order_item_id": row["order_item_id"],
                    "order_id": order["order_id"],
                    "user_id": order["user_id"],
                    "apply_date_id": _date_id(apply_time.date()),
                    "apply_time": apply_time,
                    "refund_type": "退货退款" if return_goods else "仅退款",
                    "refund_reason": reason,
                    "refund_status": status,
                    "refund_quantity": refund_qty,
                    "refund_amount": _money(amount),
                    "finish_time": finish_time.replace(microsecond=0)
                    if finish_time
                    else None,
                }
            )
    refunds.sort(key=lambda r: (r["apply_time"], r["order_item_id"]))
    for i, r in enumerate(refunds, start=1):
        r["refund_id"] = f"RF{r['apply_date_id']}{i:06d}"
    return [{"refund_id": r.pop("refund_id"), **r} for r in refunds]


def _gen_fact_user_behavior(
    rng: random.Random,
    target: int,
    order_meta: list,
    users_sorted: list[dict],
    reg_dates: list[date],
    dim_sku: list[dict],
) -> list[dict]:
    """
    生成浏览 收藏 加购行为

    每个订单明细之前都有对应商品的浏览，部分有加购和收藏，保证“买过的人一定浏览过”；
    其余名额分给没有转化的随机浏览，使整体浏览:加购 约为 100:12
    """
    start_time = datetime(START_DATE.year, START_DATE.month, START_DATE.day)
    events: list[tuple[datetime, str, str, str, str]] = []
    for order, items in order_meta:
        for row, _, _ in items:
            t_order = order["order_time"]
            for _ in range(rng.choices([1, 2, 3], [70, 25, 5])[0]):
                t = t_order - timedelta(seconds=rng.randint(60, 3 * 86400))
                events.append(
                    (
                        max(t, start_time),
                        order["user_id"],
                        row["sku_id"],
                        "浏览",
                        order["platform"],
                    )
                )
            if rng.random() < 0.20:
                t = t_order - timedelta(seconds=rng.randint(30, 3600))
                events.append(
                    (
                        max(t, start_time),
                        order["user_id"],
                        row["sku_id"],
                        "加购",
                        order["platform"],
                    )
                )
            if rng.random() < 0.04:
                t = t_order - timedelta(seconds=rng.randint(30, 86400))
                events.append(
                    (
                        max(t, start_time),
                        order["user_id"],
                        row["sku_id"],
                        "收藏",
                        order["platform"],
                    )
                )

    # 未转化的浏览：随机用户在随机一天浏览一个已上架商品
    all_days = _days(START_DATE, END_DATE)
    day_weights = [_day_weight(d) for d in all_days]
    remaining = max(target - len(events), 0)
    for _ in range(remaining):
        d = rng.choices(all_days, day_weights)[0]
        n_eligible = bisect.bisect_right(reg_dates, d)
        if n_eligible == 0:
            continue
        user = users_sorted[rng.randrange(n_eligible)]
        sku = rng.choice(dim_sku)
        if sku["launch_date"] > d:
            continue
        t = datetime(
            d.year,
            d.month,
            d.day,
            rng.choices(range(24), HOUR_WEIGHTS)[0],
            rng.randint(0, 59),
            rng.randint(0, 59),
        )
        r = rng.random()
        kind = "加购" if r < 0.045 else ("收藏" if r < 0.065 else "浏览")
        events.append((t, user["user_id"], sku["sku_id"], kind, _pick(rng, PLATFORMS)))

    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
    return [
        {
            "behavior_id": i,
            "user_id": user_id,
            "sku_id": sku_id,
            "behavior_type": kind,
            "behavior_time": t,
            "date_id": _date_id(t.date()),
            "platform": platform,
        }
        for i, (t, user_id, sku_id, kind, platform) in enumerate(events, start=1)
    ]
