"""人格引擎：5 维向量 → 权重规则 → 12 种「麦门人格」。

设计原则：**可解释、可复现、可审计**。
- 每一维的归一化方式公开（见 `_dimensions`）；
- 每个人格绑定显式阈值；
- 判定结果附带 `reasons`，说明「为什么是你」。

阈值与权重均为模块级常量，修改后重跑即可生效。
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# 人格定义
# ---------------------------------------------------------------------------

PERSONAS: dict[str, dict[str, str]] = {
    "early-bird": {
        "emoji": "🌅",
        "name": "早餐先锋",
        "en": "Breakfast Pioneer",
        "tagline": "你和麦当劳的每一天，从一份热乎的开始。",
        "desc": "早餐时段是你的主场。别人还在纠结要不要起床，你已经拿到取餐码了。"
                "你的稳定性来自规律——这份报告里，清晨是你出现最多的地方。",
        "color": "#F6A623",
    },
    "lunch-bee": {
        "emoji": "🐝",
        "name": "午间工蜂",
        "en": "Lunch Bee",
        "tagline": "工作日中午的那声取餐码，是你和城市的默契。",
        "desc": "午市是你的绝对高峰。你把午餐当成一天的中场休息，"
                "在效率与满足之间找到最短路径。你的订单里写满了「准时」。",
        "color": "#DA291C",
    },
    "midnight-muncher": {
        "emoji": "🌙",
        "name": "深夜麦客",
        "en": "Midnight Muncher",
        "tagline": "当城市熄灯，薯条的香气还在加班。",
        "desc": "你的麦当劳时刻总在夜里。无论是收工的犒赏还是赶稿的续命，"
                "你在深夜时段的下单比例远超常人——安静、专注，也有点倔强。",
        "color": "#2B3A67",
    },
    "bigmac-loyalist": {
        "emoji": "🍔",
        "name": "巨无霸主义者",
        "en": "Big Mac Loyalist",
        "tagline": "菜单千千万，你的答案从来没有变过。",
        "desc": "你对某一种口味有着近乎信仰的忠诚。选择困难在你这里不存在——"
                "你知道自己要什么，并且从不怀疑。这种确定感，本身就是一种奢侈。",
        "color": "#B4551F",
    },
    "fry-fundamentalist": {
        "emoji": "🍟",
        "name": "薯条本位者",
        "en": "Fry Fundamentalist",
        "tagline": "没有薯条的一餐，对你来说不算一餐。",
        "desc": "在你眼里，薯条不是配菜，是主菜。你的订单里它出现得如此频繁，"
                "以至于它几乎成了你与麦当劳之间的接头暗号。",
        "color": "#FFC72C",
    },
    "coupon-actuary": {
        "emoji": "🧮",
        "name": "精算麦门",
        "en": "Coupon Actuary",
        "tagline": "你打开菜单的第一件事，是看看今天有什么券。",
        "desc": "你享受的不只是食物，还有「算对」的快感。你熟练地组合优惠，"
                "把每一单的成本压到合理下限——这不是抠门，是一种对规则的掌控。",
        "color": "#1F8A70",
    },
    "points-alchemist": {
        "emoji": "⚗️",
        "name": "积分炼金术士",
        "en": "Points Alchemist",
        "tagline": "在你眼里，积分是一种会生长的资产。",
        "desc": "你清楚地知道积分从哪来、能换什么、什么时候会过期。"
                "你把消费的副产品变成了可支配的资源，这是一种被低估的能力。",
        "color": "#7B5EA7",
    },
    "feast-planner": {
        "emoji": "🎉",
        "name": "组局部长",
        "en": "Feast Planner",
        "tagline": "一个人点餐是消费，一群人点餐是节目。",
        "desc": "你的订单总是分量可观。你很可能是那个主动发起、负责点单、"
                "最后还被大家感谢的人。你不是在买吃的，你在组织一次小小的团聚。",
        "color": "#E4572E",
    },
    "value-hunter": {
        "emoji": "💰",
        "name": "极致性价比",
        "en": "Value Hunter",
        "tagline": "好吃是基础，划算才是答案。",
        "desc": "你的客单价长期保持在低位，却从不显得将就。"
                "你深谙「用最少的钱获得确定的满足」这门手艺。",
        "color": "#2E8B57",
    },
    "mcdrive-commuter": {
        "emoji": "🚗",
        "name": "车轮上的麦门",
        "en": "Drive-Thru Commuter",
        "tagline": "取餐窗口递出来的，是赶路人的续命包。",
        "desc": "得来速是你的高频场景——你不下车，不作停留，把一顿饭压缩进通勤的几分钟里。"
                "你的效率意识刻在了取餐路径上。",
        "color": "#3D5A80",
    },
    "party-host": {
        "emoji": "🎈",
        "name": "派对主办人",
        "en": "Party Host",
        "tagline": "你把生日、节日和小聚，都安排在了这里。",
        "desc": "你参与过主题活动或派对预约。比起「吃一顿」，你更在意「一起做点什么」。"
                "在你的订单史里，藏着不止一顿饭，还有几个值得纪念的日子。",
        "color": "#D1495B",
    },
    "explorer": {
        "emoji": "🧭",
        "name": "菜单探险家",
        "en": "Menu Explorer",
        "tagline": "你不重复自己，每一次点单都是一次采样。",
        "desc": "你尝过的单品数量远超常人。你大概很难接受「每次都点一样的」，"
                "而这份好奇心，让菜单对你来说永远是新大陆。",
        "color": "#00838F",
    },
    "steady-companion": {
        "emoji": "🤝",
        "name": "稳定麦伴",
        "en": "Steady Companion",
        "tagline": "不刻意，不缺席，你是最可靠的那位老客。",
        "desc": "你没有被极端特征标记，恰恰说明你心态平衡："
                "不追新、不纠结，需要的时候出现，用完就下次再见。这种「刚好」，很难得。",
        "color": "#6C757D",
    },
}

DIMENSION_LABELS: list[tuple[str, str]] = [
    ("frequency", "频次"),
    ("timing", "时段"),
    ("taste", "口味"),
    ("price", "价格带"),
    ("health", "健康结构"),
]


# ---------------------------------------------------------------------------
# 规则（按优先级从高到低）
# ---------------------------------------------------------------------------


def _r(cond: Callable[[dict[str, Any]], bool], text: str) -> tuple[Callable[[dict[str, Any]], bool], str]:
    return (cond, text)


#: 判定优先级：越靠前代表该特征越具定义性。
#: 顺序 = 节奏类 → 场景类 → 口味类 → 权益类 → 社交类 → 规模/广度类。
RULES: list[tuple[str, list[tuple[Callable[[dict[str, Any]], bool], str]]]] = [
    (
        "midnight-muncher",
        [_r(lambda s: _dp(s, "late") >= 0.25, "四分之一以上订单发生在 21:00 之后")],
    ),
    (
        "early-bird",
        [_r(lambda s: _dp(s, "breakfast") >= 0.40, "四成以上订单发生在早餐时段")],
    ),
    (
        "mcdrive-commuter",
        [_r(lambda s: s["mcdrive_rate"] >= 0.20, "超过 20% 的订单来自得来速车道")],
    ),
    (
        "lunch-bee",
        [_r(lambda s: _dp(s, "lunch") >= 0.45, "近半数订单集中在午市")],
    ),
    (
        "bigmac-loyalist",
        [_r(lambda s: s["bigmac_rate"] >= 0.30, "三成以上订单里有巨无霸")],
    ),
    (
        "fry-fundamentalist",
        [_r(lambda s: s["fries_rate"] >= 0.50, "半数以上订单里有薯条")],
    ),
    (
        "coupon-actuary",
        [_r(lambda s: s["discount_rate"] >= 0.25, "累计优惠占原价的 25% 以上")],
    ),
    (
        "points-alchemist",
        [
            _r(
                lambda s: s["mall_order_count"] >= 5 or s["points_available"] >= 10000,
                "你在积分体系里是重度参与者",
            )
        ],
    ),
    (
        "party-host",
        [_r(lambda s: s["party_count"] >= 1, "你参与过主题活动 / 派对预约")],
    ),
    (
        "feast-planner",
        [_r(lambda s: s["avg_items"] >= 4, "单笔平均件数达到 4 件以上")],
    ),
    (
        "value-hunter",
        [_r(lambda s: 0 < s["avg_ticket"] <= 25, "平均客单价不超过 ¥25")],
    ),
    (
        "explorer",
        [_r(lambda s: s["distinct_items"] >= 25, "尝过的单品数量达到 25 种以上")],
    ),
]


def _dp(s: dict[str, Any], key: str) -> float:
    for d in s["daypart_breakdown"]:
        if d["key"] == key:
            return d["pct"]
    return 0.0


# ---------------------------------------------------------------------------
# 5 维雷达
# ---------------------------------------------------------------------------


def _dimensions(s: dict[str, Any]) -> dict[str, float]:
    """把统计量归一化为 5 个 0–1 的维度值。"""
    import math

    # 频次：每月 12 单封顶
    frequency = min(1.0, s["orders_per_month"] / 12.0)

    # 时段：最集中时段的占比
    timing = max((d["pct"] for d in s["daypart_breakdown"]), default=0.0)

    # 口味：菜单广度（30 种封顶）
    taste = min(1.0, s["distinct_items"] / 30.0)

    # 价格带：以 ¥45 为满标，客单价越高越满
    price = min(1.0, s["avg_ticket"] / 45.0)

    # 健康结构：热量越高越满（仅陈述，不含价值判断）
    health = min(1.0, s["avg_calories_per_order"] / 1100.0)

    # 平滑，避免全 0 或全 1 的极端形状
    def smooth(x: float) -> float:
        return round(min(1.0, max(0.08, math.sqrt(x))), 4)

    return {
        "frequency": smooth(frequency),
        "timing": smooth(timing),
        "taste": smooth(taste),
        "price": smooth(price),
        "health": smooth(health),
    }


# ---------------------------------------------------------------------------
# 麦门指数
# ---------------------------------------------------------------------------


def _mcd_index(s: dict[str, Any]) -> int:
    """0–100 的综合「麦门指数」。"""
    parts = [
        min(1.0, s["order_count"] / 80.0) * 30,          # 陪伴时长
        min(1.0, s["distinct_items"] / 30.0) * 20,       # 探索广度
        min(1.0, s["discount_rate"] / 0.30) * 15,        # 权益利用
        min(1.0, s["points_total"] / 10000.0) * 15,      # 积分沉淀
        min(1.0, s["active_months"] / 12.0) * 20,        # 活跃持续性
    ]
    return int(round(sum(parts)))


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


def classify(s: dict[str, Any]) -> dict[str, Any]:
    """根据统计量判定人格。

    返回::

        {
          "persona": {emoji, name, en, tagline, desc, color, key},
          "reasons": [...],
          "secondary": [{emoji, name, tagline}, ...],   # 最多 2 个附加特质
          "radar": {frequency: 0..1, ...},
          "mcd_index": 0..100,
        }
    """
    primary_key: str | None = None
    reasons: list[str] = []
    matched: list[tuple[str, str]] = []

    for key, checks in RULES:
        for cond, text in checks:
            try:
                ok = bool(cond(s))
            except Exception:  # 单条规则异常不应影响整体
                ok = False
            if ok:
                matched.append((key, text))
                if primary_key is None:
                    primary_key = key
                break

    if s["order_count"] == 0:
        primary_key = None

    if primary_key is None:
        primary_key = "steady-companion"
        reasons = ["你的特征分布均衡，没有被任何单一维度极端标记"]
    else:
        reasons = [t for k, t in matched if k == primary_key][:3]

    persona = dict(PERSONAS[primary_key])
    persona["key"] = primary_key

    secondary = []
    for key, text in matched:
        if key == primary_key:
            continue
        if any(x["key"] == key for x in secondary):
            continue
        p = PERSONAS[key]
        secondary.append({"key": key, "emoji": p["emoji"], "name": p["name"], "tagline": p["tagline"]})
        if len(secondary) >= 2:
            break

    return {
        "persona": persona,
        "reasons": reasons,
        "secondary": secondary,
        "radar": _dimensions(s),
        "mcd_index": _mcd_index(s),
        "radar_labels": DIMENSION_LABELS,
    }
