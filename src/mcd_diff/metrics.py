"""指标层：把原始订单聚合为报告渲染与人格判定所需的全部统计量。

产出 💰 金钱维度（消费 / 折扣 / 客单价）与 🔥 热量维度（累计热量 / 单均 / 热量汇率 kcal/¥），
供 `persona.py` 判人格、`regret.py` 做差额汇总、`parallel_summary()` 构造「平行宇宙」对照。

本模块 **零网络、零第三方依赖、完全确定性**。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from .menu import MenuItem, benchmark_fx
from .models import RawData

# ---------------------------------------------------------------------------
# 常量（公开、可审计）
# ---------------------------------------------------------------------------

#: 时段划分：key -> (中文名, 起始小时, 结束小时)
DAYPARTS: dict[str, tuple[str, float, float]] = {
    "breakfast": ("早餐", 5.0, 10.5),
    "lunch": ("午市", 10.5, 14.0),
    "afternoon": ("下午茶", 14.0, 17.0),
    "dinner": ("晚市", 17.0, 21.0),
    "late": ("深夜", 21.0, 29.0),
}

CHANNEL_NAMES: dict[str, str] = {
    "dinein": "堂食",
    "pickup": "到店取餐",
    "delivery": "麦乐送",
    "mcdrive": "得来速",
    "party": "主题活动",
    "takeout": "外带",
}

#: 慢跑热量消耗估算（kcal / km）
KCAL_PER_KM = 60.0
#: 「一个巨无霸」的热量基准（kcal）
BIGMAC_KCAL = 550.0

#: 点单风格四象限标签：key -> (标签, 说明)
QUADRANTS: dict[str, tuple[str, str]] = {
    "value-king": ("🍚 性价比之王", "便宜且管饱——你擅长用最少的钱买到最足的能量"),
    "solid": ("💪 扎实派", "花得起也吃得饱——你不亏待自己"),
    "quality": ("🥗 品质优先", "为品质、轻食或饮品付费——你更在意「吃得精」"),
    "nibbler": ("🍡 轻食啄食", "少量尝鲜——你点得克制，也点得随性"),
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def parse_time(s: str) -> datetime | None:
    if not s:
        return None
    text = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def daypart_of(hour: float) -> str:
    h = hour % 24
    for key, (_, start, end) in DAYPARTS.items():
        if key == "late":
            if h >= 21 or h < 5:
                return key
        elif start <= h < end:
            return key
    return "late"


def _norm_name(name: str) -> str:
    return (
        name.replace("（", "(").replace("）", ")").replace(" ", "").replace("套餐", "").strip().lower()
    )


def _median(values: list[float]) -> float:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _percentile(values: list[float], q: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return vals[idx]


# ---------------------------------------------------------------------------
# 主聚合
# ---------------------------------------------------------------------------


def summarize(raw: RawData, menu: list[MenuItem] | None = None) -> dict[str, Any]:
    """把 RawData 聚合为报告所需的全部统计量。"""
    orders = raw.orders

    total_spend = sum(o.total for o in orders)
    total_discount = sum(o.discount for o in orders)
    gross = total_spend + total_discount

    item_counter: Counter[str] = Counter()
    item_display: dict[str, str] = {}
    item_price_sum: dict[str, float] = defaultdict(float)
    item_kcal: dict[str, float] = {}
    channel_counter: Counter[str] = Counter()
    daypart_counter: Counter[str] = Counter()
    hour_hist: Counter[int] = Counter()
    month_counter: Counter[str] = Counter()
    month_spend: dict[str, float] = defaultdict(float)
    month_kcal: dict[str, float] = defaultdict(float)

    total_cal = total_protein = total_fat = total_carbs = total_sodium = 0.0
    items_per_order: list[int] = []
    monthly_active: set[str] = set()
    has_nutrition = False

    order_fx: list[dict[str, Any]] = []
    dual_series: list[dict[str, Any]] = []

    for o in orders:
        channel_counter[o.channel or "dinein"] += 1

        dt = parse_time(o.time)
        month_key = ""
        if dt:
            hour_hist[dt.hour] += 1
            daypart_counter[daypart_of(dt.hour + dt.minute / 60.0)] += 1
            month_key = f"{dt.year:04d}-{dt.month:02d}"
            month_counter[month_key] += 1
            monthly_active.add(month_key)

        qty_sum = 0
        order_kcal = 0.0

        for it in o.items:
            if not it.name:
                continue
            key = _norm_name(it.name)
            if not key:
                continue
            item_counter[key] += it.qty
            item_display.setdefault(key, it.name)
            item_price_sum[key] += it.price * it.qty
            if it.calories > 0:
                item_kcal[key] = it.calories
                has_nutrition = True
            qty_sum += it.qty

            total_cal += it.calories * it.qty
            order_kcal += it.calories * it.qty
            total_protein += it.protein * it.qty
            total_fat += it.fat * it.qty
            total_carbs += it.carbs * it.qty
            total_sodium += it.sodium * it.qty

        items_per_order.append(qty_sum)

        if o.total > 0 and order_kcal > 0:
            order_fx.append(
                {
                    "order_id": o.order_id,
                    "time": o.time,
                    "date": dt.strftime("%m-%d") if dt else "",
                    "price": round(o.total, 1),
                    "calories": round(order_kcal, 1),
                    "fx": round(order_kcal / o.total, 1),
                }
            )

        if month_key:
            month_spend[month_key] += o.total
            month_kcal[month_key] += order_kcal

    order_count = len(orders)
    avg_ticket = (total_spend / order_count) if order_count else 0.0
    avg_items = (sum(items_per_order) / len(items_per_order)) if items_per_order else 0.0
    discount_rate = (total_discount / gross) if gross > 0 else 0.0

    dts = [d for d in (parse_time(o.time) for o in orders) if d is not None]
    if dts:
        first_dt, last_dt = min(dts), max(dts)
        span_days = max(1, (last_dt - first_dt).days + 1)
    else:
        first_dt = last_dt = None
        span_days = 0

    months_span = max(1, round(span_days / 30.0)) if span_days else 1
    orders_per_month = order_count / months_span if months_span else 0.0

    # ---- 月度双序列 ----
    month_series: list[tuple[str, int]] = []
    if dts:
        cursor = datetime(first_dt.year, first_dt.month, 1)
        end = datetime(last_dt.year, last_dt.month, 1)
        while cursor <= end:
            k = f"{cursor.year:04d}-{cursor.month:02d}"
            month_series.append((k, month_counter.get(k, 0)))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_series = month_series[-12:]

    for k, _ in month_series:
        dual_series.append(
            {
                "month": k,
                "orders": month_counter.get(k, 0),
                "spend": round(month_spend.get(k, 0.0), 1),
                "calories": round(month_kcal.get(k, 0.0), 1),
            }
        )

    # ---- 单品矩阵 + 四象限 ----
    item_matrix: list[dict[str, Any]] = []
    raw_matrix: list[dict[str, Any]] = []
    for key, count in item_counter.items():
        price = item_price_sum[key] / count if count else 0.0
        kcal = item_kcal.get(key, 0.0)
        raw_matrix.append(
            {
                "name": item_display.get(key, key),
                "count": count,
                "price": round(price, 2),
                "calories": round(kcal, 1),
                "fx": round(kcal / price, 1) if price > 0 else 0.0,
            }
        )

    with_fx = [m for m in raw_matrix if m["fx"] > 0]
    if with_fx:
        price_mid = _median([m["price"] for m in with_fx])
        fx_mid = _median([m["fx"] for m in with_fx])
        for m in with_fx:
            hi_price = m["price"] >= price_mid
            hi_fx = m["fx"] >= fx_mid
            if hi_price and hi_fx:
                q = "solid"
            elif not hi_price and hi_fx:
                q = "value-king"
            elif hi_price and not hi_fx:
                q = "quality"
            else:
                q = "nibbler"
            m["quadrant"] = q
            m["quadrant_label"] = QUADRANTS[q][0]
            item_matrix.append(m)

        q_counter: Counter[str] = Counter()
        for m in item_matrix:
            q_counter[m["quadrant"]] += m["count"]
        total_hits = sum(q_counter.values()) or 1
        quadrant_summary = [
            {
                "key": k,
                "label": QUADRANTS[k][0],
                "desc": QUADRANTS[k][1],
                "count": q_counter.get(k, 0),
                "pct": q_counter.get(k, 0) / total_hits,
            }
            for k in ("value-king", "solid", "quality", "nibbler")
        ]
        # 风格位：按出现频次加权的质心（用于散点图标出"你在这里"）
        style_position = {
            "price": round(sum(m["price"] * m["count"] for m in item_matrix) / total_hits, 2),
            "fx": round(sum(m["fx"] * m["count"] for m in item_matrix) / total_hits, 1),
            "price_mid": price_mid,
            "fx_mid": fx_mid,
            "quadrant": max(quadrant_summary, key=lambda x: x["count"])["key"],
            "quadrant_label": max(quadrant_summary, key=lambda x: x["count"])["label"],
        }
    else:
        quadrant_summary = []
        style_position = {}

    # ---- 热量汇率 (Calorie FX) ----
    fx_personal = (total_cal / total_spend) if total_spend > 0 else 0.0
    fx_values = [o["fx"] for o in order_fx]
    fx_bench = benchmark_fx(menu) if menu else 0.0
    best_fx = max(order_fx, key=lambda o: o["fx"]) if order_fx else None
    low_fx = min(order_fx, key=lambda o: o["fx"]) if order_fx else None

    top_items = [
        {"name": item_display.get(k, k), "count": v} for k, v in item_counter.most_common(5)
    ]

    channel_breakdown = [
        {
            "key": k,
            "name": CHANNEL_NAMES.get(k, k),
            "count": v,
            "pct": (v / order_count) if order_count else 0.0,
        }
        for k, v in channel_counter.most_common()
    ]

    daypart_breakdown = [
        {
            "key": key,
            "name": meta[0],
            "count": daypart_counter.get(key, 0),
            "pct": (daypart_counter.get(key, 0) / order_count) if order_count else 0.0,
        }
        for key, meta in DAYPARTS.items()
    ]

    hour_breakdown = [
        {"hour": h, "count": hour_hist.get(h, 0)} for h in list(range(15, 24)) + list(range(0, 15))
    ]

    def hit_rate(keyword: str) -> float:
        if not order_count:
            return 0.0
        hit = sum(1 for o in orders if any(keyword in (it.name or "") for it in o.items))
        return hit / order_count

    summary: dict[str, Any] = {
        # 规模
        "order_count": order_count,
        "distinct_items": len(item_counter),
        "item_occurrences": sum(item_counter.values()),
        "span_days": span_days,
        "months_span": months_span,
        "orders_per_month": orders_per_month,
        "window": {
            "start": first_dt.strftime("%Y-%m-%d") if first_dt else "—",
            "end": last_dt.strftime("%Y-%m-%d") if last_dt else "—",
        },
        # 💰 金钱账户
        "money_account": {
            "total_spend": round(total_spend, 1),
            "total_discount": round(total_discount, 1),
            "gross": round(gross, 1),
            "discount_rate": discount_rate,
            "avg_ticket": round(avg_ticket, 2),
            "per_day": round(total_spend / span_days, 2) if span_days else 0.0,
            "orders_per_month": round(orders_per_month, 1),
        },
        # 🔥 热量账户
        "calorie_account": {
            "total_calories": round(total_cal, 1),
            "per_order": round(total_cal / order_count, 1) if order_count else 0.0,
            "per_day": round(total_cal / span_days, 1) if span_days else 0.0,
            "protein": round(total_protein, 1),
            "fat": round(total_fat, 1),
            "carbs": round(total_carbs, 1),
            "sodium": round(total_sodium, 1),
            "run_km": round(total_cal / KCAL_PER_KM, 1) if total_cal else 0.0,
            "bigmac_equivalent": round(total_cal / BIGMAC_KCAL, 1) if total_cal else 0.0,
        },
        # 💱 热量汇率
        "fx": {
            "personal": round(fx_personal, 1),
            "benchmark": round(fx_bench, 1),
            "vs_benchmark": round(fx_personal - fx_bench, 1) if fx_bench else 0.0,
            "p25": _percentile(fx_values, 0.25),
            "median": _percentile(fx_values, 0.5),
            "p75": _percentile(fx_values, 0.75),
            "best": best_fx,
            "lowest": low_fx,
            "orders": order_fx,
        },
        # 结构
        "top_items": top_items,
        "item_matrix": item_matrix,
        "quadrant_summary": quadrant_summary,
        "style_position": style_position,
        "channel_breakdown": channel_breakdown,
        "daypart_breakdown": daypart_breakdown,
        "hour_breakdown": hour_breakdown,
        "month_series": [{"month": m, "count": c} for m, c in month_series],
        "month_dual": dual_series,
        "active_months": len(monthly_active),
        # 权益
        "points_available": raw.account.points_available,
        "points_total": raw.account.points_total,
        "points_expiring": raw.account.points_expiring,
        "mall_order_count": len(raw.mall_orders),
        "points_spent": sum(m.points_cost for m in raw.mall_orders),
        "coupon_count": len(raw.coupons),
        # 判定特征
        "bigmac_rate": hit_rate("巨无霸"),
        "fries_rate": hit_rate("薯条"),
        "mcdrive_rate": (channel_counter.get("mcdrive", 0) / order_count) if order_count else 0.0,
        "party_count": channel_counter.get("party", 0),
        # 兼容旧字段
        "total_spend": round(total_spend, 1),
        "total_discount": round(total_discount, 1),
        "gross": round(gross, 1),
        "discount_rate": discount_rate,
        "avg_ticket": round(avg_ticket, 2),
        "avg_items": round(avg_items, 2),
        "has_nutrition": has_nutrition,
        "total_calories": round(total_cal, 1),
        "avg_calories_per_order": round(total_cal / order_count, 1) if order_count else 0.0,
        "total_protein": round(total_protein, 1),
        "total_fat": round(total_fat, 1),
        "total_carbs": round(total_carbs, 1),
        "total_sodium": round(total_sodium, 1),
        "run_km": round(total_cal / KCAL_PER_KM, 1) if total_cal else 0.0,
        "bigmac_equivalent": round(total_cal / BIGMAC_KCAL, 1) if total_cal else 0.0,
    }

    summary["badges"] = compute_badges(summary)
    return summary


def compute_badges(s: dict[str, Any]) -> list[dict[str, str]]:
    """根据统计量解锁成就徽章。"""
    badges: list[dict[str, str]] = []

    dp = {d["key"]: d["count"] for d in s["daypart_breakdown"]}
    if dp.get("breakfast", 0) >= 10:
        badges.append({"emoji": "🌅", "name": "早八人", "desc": f"早餐时段下单 {dp['breakfast']} 次"})
    if dp.get("late", 0) >= 5:
        badges.append({"emoji": "🌙", "name": "零点麦", "desc": f"深夜时段下单 {dp['late']} 次"})
    if s["total_discount"] >= 150:
        badges.append({"emoji": "🧮", "name": "券王", "desc": f"累计优惠 ¥{s['total_discount']:.0f}"})
    if s["points_available"] >= 5000:
        badges.append({"emoji": "⚗️", "name": "积分大户", "desc": f"可用积分 {s['points_available']:.0f}"})
    if s["mall_order_count"] >= 3:
        badges.append({"emoji": "🎁", "name": "兑换达人", "desc": f"商城兑换 {s['mall_order_count']} 次"})
    if s["distinct_items"] >= 25:
        badges.append({"emoji": "🧭", "name": "菜单漫游者", "desc": f"尝过 {s['distinct_items']} 种单品"})
    if s["mcdrive_rate"] >= 0.2:
        badges.append({"emoji": "🚗", "name": "得来速常客", "desc": "两成以上订单来自得来速"})
    if s["active_months"] >= 10:
        badges.append({"emoji": "📅", "name": "全年在线", "desc": f"{s['active_months']} 个月都有下单"})
    if s["avg_items"] >= 5:
        badges.append({"emoji": "🎉", "name": "组局能手", "desc": f"单笔平均 {s['avg_items']:.1f} 件"})
    if s["order_count"] >= 60:
        badges.append({"emoji": "🔀", "name": "重开素材库", "desc": f"累计 {s['order_count']} 单可供重开"})

    # ---- 双账簿新增徽章 ----
    fx = s["fx"]
    if fx["orders"]:
        spread = fx["p75"] - fx["p25"]
        if spread >= 12:
            badges.append(
                {"emoji": "💱", "name": "汇率操盘手", "desc": f"订单间汇率跨度 {spread:.0f} kcal/¥"}
            )
    qmap = {q["key"]: q for q in s["quadrant_summary"]}
    if qmap.get("value-king", {}).get("pct", 0) >= 0.40:
        badges.append({"emoji": "🍚", "name": "性价比之王", "desc": "四成以上支出落在高汇率象限"})
    if qmap.get("quality", {}).get("pct", 0) >= 0.40:
        badges.append({"emoji": "🥗", "name": "品质优先", "desc": "四成以上支出落在品质象限"})
    if qmap.get("solid", {}).get("pct", 0) >= 0.40:
        badges.append({"emoji": "💪", "name": "扎实派", "desc": "四成以上支出落在扎实象限"})

    return badges
