"""数据模型：把麦当劳 MCP 的返回结构映射为强类型对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    """尽力把值转为 float，失败则回退默认值。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass
class OrderItem:
    """订单中的单个餐品（已归一到单品粒度）。"""

    name: str
    qty: int = 1
    price: float = 0.0
    calories: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0
    sodium: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OrderItem":
        return cls(
            name=str(d.get("name") or d.get("item_name") or "").strip(),
            qty=max(1, _i(d.get("qty") or d.get("quantity"), 1)),
            price=_f(d.get("price") or d.get("unit_price")),
            calories=_f(d.get("calories") or d.get("kcal") or d.get("energy")),
            protein=_f(d.get("protein")),
            fat=_f(d.get("fat")),
            carbs=_f(d.get("carbs") or d.get("carbohydrate")),
            sodium=_f(d.get("sodium")),
        )


@dataclass
class Order:
    """一笔订单。"""

    order_id: str = ""
    time: str = ""            # ISO 8601 字符串
    channel: str = "dinein"   # dinein / pickup / delivery / mcdrive / party
    store: str = ""
    total: float = 0.0        # 实付金额
    discount: float = 0.0     # 优惠金额
    items: list[OrderItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Order":
        return cls(
            order_id=str(d.get("order_id") or d.get("orderId") or ""),
            time=str(d.get("time") or d.get("orderTime") or d.get("created_at") or ""),
            channel=str(d.get("channel") or d.get("orderChannel") or "dinein").lower(),
            store=str(d.get("store") or d.get("storeName") or ""),
            total=_f(d.get("total") or d.get("payAmount") or d.get("totalAmount")),
            discount=_f(d.get("discount") or d.get("discountAmount")),
            items=[OrderItem.from_dict(i) for i in (d.get("items") or []) if isinstance(i, dict)],
        )


@dataclass
class Account:
    """积分账户。"""

    points_available: float = 0.0
    points_total: float = 0.0
    points_expiring: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Account":
        d = d or {}
        return cls(
            points_available=_f(d.get("points_available") or d.get("availablePoints")),
            points_total=_f(d.get("points_total") or d.get("totalPoints")),
            points_expiring=_f(d.get("points_expiring") or d.get("expiringPoints")),
        )


@dataclass
class Coupon:
    name: str = ""
    status: str = "available"
    value: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Coupon":
        return cls(
            name=str(d.get("name") or ""),
            status=str(d.get("status") or "available"),
            value=_f(d.get("value")),
        )


@dataclass
class MallOrder:
    """麦麦商城积分兑换记录。"""

    product_name: str = ""
    points_cost: float = 0.0
    created_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MallOrder":
        return cls(
            product_name=str(d.get("product_name") or d.get("productName") or ""),
            points_cost=_f(d.get("points_cost") or d.get("pointsCost")),
            created_at=str(d.get("created_at") or d.get("createdAt") or ""),
        )


@dataclass
class RawData:
    """一次完整采集的结果。"""

    meta: dict[str, Any] = field(default_factory=dict)
    account: Account = field(default_factory=Account)
    orders: list[Order] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    mall_orders: list[MallOrder] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawData":
        return cls(
            meta=dict(d.get("meta") or {}),
            account=Account.from_dict(d.get("account")),
            orders=[Order.from_dict(o) for o in (d.get("orders") or []) if isinstance(o, dict)],
            coupons=[Coupon.from_dict(c) for c in (d.get("coupons") or []) if isinstance(c, dict)],
            mall_orders=[
                MallOrder.from_dict(m) for m in (d.get("mall_orders") or []) if isinstance(m, dict)
            ],
        )
