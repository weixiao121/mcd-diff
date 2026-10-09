"""生成脱敏示例数据 examples/sample_raw.json（虚构数据，用于离线演示与测试）。

价格与营养数据直接读取 `data/menu.json`，保证示例与求解器所见一致。

运行：
    python examples/generate_sample_data.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_diff.menu import load_menu  # noqa: E402
from mcd_diff.solver import MAX_COMBO_SIZE  # noqa: E402

SEED = 1024
random.seed(SEED)

OUT = Path(__file__).parent / "sample_raw.json"

MENU = {i.name: i for i in load_menu()}

# 套餐 -> 单品组合
# ⚠️ 组合里的单品名必须与 data/menu.json 完全一致（该菜单由 MCP 实拉生成），
#    否则示例数据会引用到菜单里不存在的餐品、求解器就找不到替代方案。
COMBOS = {
    # —— 正餐 ——
    "巨无霸套餐": ["巨无霸", "薯条", "可乐"],
    "板烧鸡腿堡套餐": ["板烧鸡腿堡", "薯条", "雪碧"],
    "麦辣鸡腿堡套餐": ["麦辣鸡腿汉堡", "薯条", "可乐"],
    "双层吉士套餐": ["双层吉士汉堡", "薯条", "可乐"],
    "安格斯套餐": ["芝士安格斯厚牛堡", "薯条", "可乐"],
    "培根安格斯套餐": ["培根安格斯厚牛堡", "薯条", "可乐"],
    "麦乐鸡分享装": ["麦乐鸡", "薯条", "薄皮焦香V翅", "可乐"],
    "麦香鱼套餐": ["麦香鱼", "玉米杯", "雪碧"],
    # —— 早餐（麦满分系列）——
    "早餐组合A": ["猪柳蛋麦满分", "鲜萃咖啡"],
    "早餐组合B": ["双层猪柳蛋麦满分", "优品豆浆"],
    "早餐组合C": ["大脆鸡扒麦满分", "脆薯饼"],
    "早餐组合D": ["原味板烧鸡腿麦满分", "脆香油条", "鲜萃咖啡"],
    "早餐组合E": ["猪柳炒双蛋堡", "纯牛奶(盒装)"],
    # —— 其他时段 ——
    "下午茶组合": ["新地", "派", "鲜萃咖啡"],
    "轻享组合": ["玉米杯", "麦香鱼", "苹果片"],
    "晚市组合": ["板烧鸡腿堡", "玉米杯", "雪碧"],
    "宵夜组合": ["酥酥多笋卷", "那么大鸡排（椒盐风味）", "可乐"],
}

#: 可随机加购的单品
EXTRAS = ["派", "新地", "玉米杯", "薯条", "薄皮焦香V翅", "苹果片", "圆筒冰淇淋"]

#: 组局场景额外加的
PARTY_EXTRA = ["薯条", "麦乐鸡", "可乐", "新地", "薄皮焦香V翅"]

CHANNELS = ["dinein", "pickup", "delivery", "mcdrive", "party"]
CHANNEL_WEIGHTS = [0.28, 0.40, 0.22, 0.08, 0.02]

STORES = [
    "上海南京西路店", "上海徐家汇店", "上海陆家嘴环路店", "北京国贸店",
    "深圳福田中心店", "广州天河城店", "杭州武林广场店",
]

HOUR_BUCKETS = {
    "breakfast": ([7, 8, 8, 9], 0.50),
    "lunch": ([11, 12, 12, 12, 13], 0.28),
    "afternoon": ([15, 16, 16, 17], 0.06),
    "dinner": ([18, 19, 19, 20], 0.10),
    "late": ([21, 22, 22, 23], 0.06),
}


def pick_hour() -> int:
    buckets = list(HOUR_BUCKETS.keys())
    weights = [HOUR_BUCKETS[b][1] for b in buckets]
    bucket = random.choices(buckets, weights=weights, k=1)[0]
    return random.choice(HOUR_BUCKETS[bucket][0])


def build_order(idx: int, dt: datetime) -> dict:
    channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0]

    if dt.hour < 11:
        combo_name = random.choice(
            ["早餐组合A", "早餐组合B", "早餐组合C", "早餐组合D", "早餐组合E", "早餐组合A"]
        )
    elif dt.hour >= 21:
        combo_name = random.choice(["巨无霸套餐", "麦乐鸡分享装", "宵夜组合", "晚市组合"])
    else:
        combo_name = random.choice([c for c in COMBOS if not c.startswith("早餐")])

    names = list(COMBOS[combo_name])
    if random.random() < 0.22:
        names.append(random.choice(EXTRAS))
    # ⚠️ 订单件数上限与求解器一致（MAX_COMBO_SIZE = 4）。
    #    否则原单 5 件、重开最多 4 件，对照就不公平了。
    while channel == "party" and len(names) < MAX_COMBO_SIZE:
        names.append(random.choice(PARTY_EXTRA))
    names = names[:MAX_COMBO_SIZE]

    # 订单级折扣按比例分摊到单品，得到「实付单价」
    rate = random.uniform(0.0, 0.26)
    gross = sum(MENU[n].price for n in names)
    items = []
    for n in names:
        m = MENU[n]
        items.append(
            {
                "name": n,
                "qty": 1,
                "price": round(m.price * (1 - rate), 2),
                "calories": m.calories,
                "protein": m.protein,
                "fat": m.fat,
                "carbs": m.carbs,
                "sodium": m.sodium,
            }
        )

    return {
        "order_id": f"MC{dt.strftime('%Y%m%d')}{idx:04d}",
        "time": dt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "channel": channel,
        "store": random.choice(STORES),
        "total": round(gross * (1 - rate), 2),
        "discount": round(gross * rate, 2),
        "items": items,
    }


def main() -> None:
    start = datetime(2025, 12, 26, 12, 0)
    end = datetime(2026, 10, 9, 20, 0)
    span_days = (end - start).days

    orders = []
    for i in range(74):
        r = random.betavariate(1.6, 1.0)
        dt = start + timedelta(days=r * span_days)
        dt = dt.replace(hour=pick_hour(), minute=random.choice([5, 12, 18, 24, 31, 38, 46, 53]))
        if dt > end:
            dt = end - timedelta(days=random.randint(1, 20))
        orders.append(build_order(i, dt))

    orders.sort(key=lambda o: o["time"])

    coupons = [
        {"name": "巨无霸立减 5 元", "status": "used", "value": 5},
        {"name": "任选第二份半价", "status": "used", "value": 8},
        {"name": "满 30 减 8", "status": "available", "value": 8},
        {"name": "麦乐送免配送费", "status": "available", "value": 9},
    ]

    mall_orders = [
        {"product_name": "麦麦商城·定制帆布袋", "points_cost": 2800, "created_at": "2026-03-18T10:22:00+08:00"},
        {"product_name": "麦麦商城·经典马克杯", "points_cost": 3200, "created_at": "2026-05-06T19:40:00+08:00"},
        {"product_name": "麦乐送免配送费券", "points_cost": 600, "created_at": "2026-07-22T12:05:00+08:00"},
        {"product_name": "麦麦商城·联名贴纸包", "points_cost": 800, "created_at": "2026-08-30T16:10:00+08:00"},
        {"product_name": "麦乐送免配送费券", "points_cost": 600, "created_at": "2026-09-14T11:35:00+08:00"},
        {"product_name": "麦麦商城·保温杯", "points_cost": 4800, "created_at": "2026-09-28T09:12:00+08:00"},
    ]

    payload = {
        "meta": {
            "generated_at": datetime(2026, 10, 9, 20, 30).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "source": "mcd-mcp",
            "window_days": 365,
            "note": "示例数据由脚本随机生成，为虚构数据，不含任何真实用户信息。",
        },
        "account": {"points_available": 5820, "points_total": 12400, "points_expiring": 420},
        "orders": orders,
        "coupons": coupons,
        "mall_orders": mall_orders,
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已生成示例数据：{OUT}（{len(orders)} 单）")


if __name__ == "__main__":
    main()
