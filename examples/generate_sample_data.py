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

SEED = 1024
random.seed(SEED)

OUT = Path(__file__).parent / "sample_raw.json"

MENU = {i.name: i for i in load_menu()}

# 套餐 -> 单品组合
COMBOS = {
    "巨无霸套餐": ["巨无霸", "中薯条", "可口可乐(中)"],
    "板烧鸡腿堡套餐": ["板烧鸡腿堡", "中薯条", "雪碧(中)"],
    "麦辣鸡腿堡套餐": ["麦辣鸡腿汉堡", "中薯条", "可口可乐(中)"],
    "双层吉士套餐": ["双层吉士汉堡", "中薯条", "可口可乐(中)"],
    "麦乐鸡分享装": ["麦乐鸡(5块)", "大薯条", "麦辣鸡翅(2块)", "可口可乐(中)"],
    "安格斯套餐": ["安格斯厚牛堡", "大薯条", "可口可乐(中)"],
    "早餐组合A": ["吉士蛋麦满分", "鲜煮咖啡"],
    "早餐组合B": ["猪柳蛋麦满分", "鲜煮咖啡"],
    "早餐组合C": ["热香饼(3片)", "鲜煮咖啡"],
    "下午茶组合": ["麦旋风", "香芋派", "拿铁"],
    "轻享组合": ["玉米杯", "麦香鱼", "橙汁"],
    "晚市组合": ["板烧鸡腿堡", "玉米杯", "雪碧(中)"],
}

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
        combo_name = random.choice(["早餐组合A", "早餐组合B", "早餐组合C", "早餐组合A"])
    elif dt.hour >= 21:
        combo_name = random.choice(["巨无霸套餐", "麦乐鸡分享装", "晚市组合"])
    else:
        combo_name = random.choice(list(COMBOS.keys()))

    names = list(COMBOS[combo_name])
    if random.random() < 0.22:
        names.append(random.choice(["香芋派", "麦旋风", "玉米杯", "麦辣鸡翅(2块)"]))
    if channel == "party":
        names += random.sample(["大薯条", "麦乐鸡(5块)", "可口可乐(中)", "麦旋风"], k=3)

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
