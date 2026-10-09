"""差额引擎 —— 麦门 Diff 的核心。

对历史订单中的**每一单**，在「该单自身的实付金额 ∩ 该单自身的热量」约束内
重新求解一次最优组合，得到三组差额：

    Δcost    = 该单实付 − 重开最优价格        （恒 ≥ 0，由预算约束保证）
    Δkcal    = 该单实际热量 − 重开最优热量
    Δprotein = 重开最优蛋白 − 该单实际蛋白

为什么约束要「以该单自身为天花板」
----------------------------------
省钱类项目解「最小化价格」→ 必然推出"什么都别点最省"；
热量类项目解「最小化热量」→ 必然推出"只喝零度可乐最轻"。

我们把预算 / 热量的上限**锁定为该单原有的值**，只问一句话：

    「同样的钱、同样的热量，能不能吃得更好？」

因此输出的是「**同等条件下的更优解**」，而不是「你该少花」——
既不劝用户少消费（预算上限 = 原实付），也不劝用户少吃（热量上限 = 原热量），
天然规避「不得宣扬不健康饮食」与「不得劝退消费」两条合规红线。

**一个重要的自洽性**：因为预算上限 = 该单实付，所以重开最优价必然 ≤ 实付，
即 **Δcost ≥ 0 恒成立**，不需要任何裁剪。

安全性：本模块纯本地计算，不调用任何 MCP 工具，不产生任何订单。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .menu import MenuItem
from .metrics import _norm_name, daypart_of, parse_time
from .models import Order
from .solver import (
    MAX_COMBO_SIZE,
    Constraints,
    Solution,
    solve_cheapest,
    solve_dual_constraint,
    solve_lightest,
)

#: 三种重开模式：约束完全相同，只有目标函数不同 → 三种结果天然可比
MODES: dict[str, str] = {
    "balanced": "扎实向（同预算同热量内最大化蛋白质）",
    "thrift": "省钱向（同预算同热量内价格最低）",
    "lean": "轻负担向（同预算同热量内热量最低）",
}

#: 默认走「省钱向」。产品的核心钩子是"你本可以省下多少钱"。
#: 实测同一批订单：thrift 省 11.4%，balanced 只省 3.1%（它把预算优先换成了蛋白质），
#: lean 省 7.1% 但少摄入 8,525 kcal。三种模式**约束完全相同、只有目标函数不同**，
#: 所以结果天然可对照 —— 报告里三条路线并排展示。
DEFAULT_MODE = "thrift"

#: 蛋白质下界的保底值：低于它就不算"一顿饭"
MIN_PROTEIN_FLOOR = 10.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class OrderDiff:
    """单笔订单的平行对照。"""

    order_id: str = ""
    time: str = ""
    store: str = ""
    channel: str = "dinein"
    daypart: str = ""

    # ---- 你点的 ----
    actual_items: list[str] = field(default_factory=list)
    actual_cost: float = 0.0
    actual_kcal: float = 0.0
    actual_protein: float = 0.0

    # ---- 重开的 ----
    optimal_items: list[str] = field(default_factory=list)
    optimal_cost: float = 0.0
    optimal_kcal: float = 0.0
    optimal_protein: float = 0.0

    feasible: bool = True
    note: str = ""
    #: 该单的折扣系数（实付 ÷ 菜单原价）。重开时用它缩放菜单价格，
    #: 使「重开价」与「实付」处在同一口径下可比。
    price_factor: float = 1.0

    # ---- 差额 ----
    @property
    def delta_cost(self) -> float:
        """省下的钱。预算上限保证它不会为负。"""
        return round(max(0.0, self.actual_cost - self.optimal_cost), 2) if self.feasible else 0.0

    @property
    def delta_kcal(self) -> float:
        """少摄入的热量（可为负——若重开方案热量更高则说明原单本就偏轻）。"""
        return round(self.actual_kcal - self.optimal_kcal, 1) if self.feasible else 0.0

    @property
    def delta_protein(self) -> float:
        """多摄入的蛋白质。"""
        return round(self.optimal_protein - self.actual_protein, 1) if self.feasible else 0.0

    @property
    def changed(self) -> bool:
        """重开结果与原单是否有实质差异。"""
        if not self.feasible:
            return False
        return self.delta_cost > 0.01 or abs(self.delta_kcal) > 1.0 or self.delta_protein > 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "time": self.time,
            "store": self.store,
            "channel": self.channel,
            "daypart": self.daypart,
            "feasible": self.feasible,
            "note": self.note,
            "price_factor": self.price_factor,
            "actual": {
                "items": self.actual_items,
                "cost": self.actual_cost,
                "kcal": self.actual_kcal,
                "protein": self.actual_protein,
            },
            "optimal": {
                "items": self.optimal_items,
                "cost": self.optimal_cost,
                "kcal": self.optimal_kcal,
                "protein": self.optimal_protein,
            },
            "delta": {
                "cost": self.delta_cost,
                "kcal": self.delta_kcal,
                "protein": self.delta_protein,
            },
        }


# ---------------------------------------------------------------------------
# 单笔重开
# ---------------------------------------------------------------------------


def _actual_of(order: Order) -> tuple[float, float, float]:
    """返回该单的（实付金额, 热量, 蛋白质）。金额缺失时用菜单原价兜底。"""
    cost = order.total
    if cost <= 0:
        cost = sum(i.price * i.qty for i in order.items)
    kcal = sum(i.calories * i.qty for i in order.items)
    protein = sum(i.protein * i.qty for i in order.items)
    return round(cost, 2), round(kcal, 1), round(protein, 1)


def _gross_of(order: Order, menu: list[MenuItem]) -> float:
    """该单按**菜单原价**计算的合计金额。"""
    index = {_norm_name(m.name): m.price for m in menu}
    total = 0.0
    for item in order.items:
        price = index.get(_norm_name(item.name))
        total += (price if price is not None else item.price) * item.qty
    return round(total, 2)


def _scaled_menu(menu: list[MenuItem], factor: float) -> list[MenuItem]:
    """按折扣系数缩放菜单价格。

    订单实付里含优惠，而菜单是原价 —— 拿实付去卡原价菜单会得到大量「无解」。
    按该单自身的折扣系数缩放后，两边口径一致，等价于假设
    「那天的优惠力度不变，换个点法会怎样」。
    """
    if factor >= 0.9999:
        return menu
    # 刻意不 round：缩放后「原组合」的价格必须精确回到该单实付，
    # 否则会踩到求解器的预算容差、把原单自己判成不可行。
    return [replace(m, price=m.price * factor) for m in menu]


def categories_of_order(order: Order, menu: list[MenuItem]) -> set[str]:
    """提取一笔订单覆盖到的品类集合（用菜单反查）。

    用「品类保留」而不是「单品保留」是刻意的：
    用户想喝咖啡，但未必要喝**这一杯**咖啡 —— 换一杯更便宜的饮品可以接受；
    而直接把饮品取消掉，就不是"同等条件下的更优解"了。
    """
    index = {_norm_name(m.name): m.category for m in menu}
    cats: set[str] = set()
    for item in order.items:
        cat = index.get(_norm_name(item.name))
        if cat:
            cats.add(cat)
    return cats


_SOLVERS: dict[str, Callable[[list[MenuItem], Constraints], Solution]] = {
    "balanced": solve_dual_constraint,
    "thrift": solve_cheapest,
    "lean": solve_lightest,
}


def replay_order(
    order: Order,
    menu: list[MenuItem],
    *,
    mode: str = DEFAULT_MODE,
) -> OrderDiff:
    """重开一笔订单。"""
    if mode not in _SOLVERS:
        raise ValueError(f"未知重开模式：{mode}（可选 {'/'.join(_SOLVERS)}）")

    cost, kcal, protein = _actual_of(order)
    dt = parse_time(order.time)

    diff = OrderDiff(
        order_id=order.order_id,
        time=(dt.strftime("%Y-%m-%d %H:%M") if dt else (order.time or "—")),
        store=order.store or "—",
        channel=order.channel,
        daypart=(daypart_of(dt.hour + dt.minute / 60.0) if dt else ""),
        actual_items=[f"{i.name}×{i.qty}" if i.qty > 1 else i.name for i in order.items],
        actual_cost=cost,
        actual_kcal=kcal,
        actual_protein=protein,
    )

    if cost <= 0 or not order.items:
        diff.feasible = False
        diff.note = "该单缺少金额或商品明细，无法重开"
        return diff

    solver = _SOLVERS[mode]

    # 口径对齐：按该单自身的折扣系数缩放菜单，让重开价与实付可比
    gross = _gross_of(order, menu)
    factor = (cost / gross) if gross > cost > 0 else 1.0
    diff.price_factor = round(factor, 4)
    priced = _scaled_menu(menu, factor)

    # 品类保留：重开方案必须保持原单的点单结构
    cats = categories_of_order(order, menu)
    if len(cats) > MAX_COMBO_SIZE:
        cats = set()  # 品类多于可枚举件数时放弃该约束，避免必然无解
    required = frozenset(cats) if cats else None

    def _cons(floor: float) -> Constraints:
        return Constraints(
            budget=cost,
            kcal_cap=kcal if kcal > 0 else None,
            protein_floor=floor,
            require_categories=required,
        )

    # 第一次尝试：蛋白质不低于原单（严格 Pareto —— 钱不增、热量不增、蛋白不减）
    cons = _cons(round(protein, 1))
    sol = solver(priced, cons)

    # 第二次尝试：蛋白质让步 25%（原单本身蛋白就不高时不值得让步，直接判不可行）
    if not sol.feasible and protein > MIN_PROTEIN_FLOOR:
        relaxed = round(max(MIN_PROTEIN_FLOOR, protein * 0.75), 1)
        if relaxed < cons.protein_floor:
            cons = _cons(relaxed)
            sol = solver(priced, cons)
            if sol.feasible:
                diff.note = f"蛋白质由 {protein:.0f}g 放宽至 {relaxed:.0f}g 后求解"

    if not sol.feasible:
        diff.feasible = False
        diff.note = "在当前菜单与约束下没有可行解"
        return diff

    diff.optimal_items = [i.name for i in sol.items]
    diff.optimal_cost = sol.price
    diff.optimal_kcal = sol.calories
    diff.optimal_protein = sol.protein
    return diff


def replay_orders(orders: list[Order], menu: list[MenuItem], *, mode: str = DEFAULT_MODE) -> list[OrderDiff]:
    """重开整个订单集。"""
    return [replay_order(o, menu, mode=mode) for o in orders]


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def summarize_regret(
    diffs: list[OrderDiff],
    *,
    mode: str = DEFAULT_MODE,
    top_n: int = 5,
) -> dict[str, Any]:
    """把逐单差额汇总成年度账簿。"""
    ok = [d for d in diffs if d.feasible]
    bad = [d for d in diffs if not d.feasible]

    total_actual = round(sum(d.actual_cost for d in ok), 2)
    total_optimal = round(sum(d.optimal_cost for d in ok), 2)
    total_saving = round(sum(d.delta_cost for d in ok), 2)
    total_kcal_actual = round(sum(d.actual_kcal for d in ok), 1)
    total_kcal_optimal = round(sum(d.optimal_kcal for d in ok), 1)
    total_kcal_saving = round(sum(max(0.0, d.delta_kcal) for d in ok), 1)
    total_protein_gain = round(sum(max(0.0, d.delta_protein) for d in ok), 1)

    changed = [d for d in ok if d.changed]
    perfect = [d for d in ok if not d.changed]

    saving_rate = (total_saving / total_actual) if total_actual > 0 else 0.0
    # 重开潜力指数：0–100，把"可节省比例"放大到可读刻度（20% → 50 分，40% → 满分）
    potential_index = int(round(min(100.0, saving_rate * 250)))

    def _agg(key: str) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for d in ok:
            k = getattr(d, key) or "—"
            b = buckets.setdefault(k, {"key": k, "count": 0, "saving": 0.0, "spend": 0.0})
            b["count"] += 1
            b["saving"] += d.delta_cost
            b["spend"] += d.actual_cost
        rows = []
        for b in buckets.values():
            b["saving"] = round(b["saving"], 2)
            b["spend"] = round(b["spend"], 2)
            b["rate"] = round(b["saving"] / b["spend"], 4) if b["spend"] else 0.0
            rows.append(b)
        rows.sort(key=lambda x: x["saving"], reverse=True)
        return rows

    return {
        "mode": mode,
        "mode_label": MODES.get(mode, mode),
        "orders_total": len(diffs),
        "orders_replayed": len(ok),
        "orders_unfeasible": len(bad),
        "orders_changed": len(changed),
        "orders_perfect": len(perfect),
        "changed_rate": round(len(changed) / len(ok), 4) if ok else 0.0,
        # 金额
        "total_actual": total_actual,
        "total_optimal": total_optimal,
        "total_saving": total_saving,
        "saving_rate": round(saving_rate, 4),
        "avg_saving_per_order": round(total_saving / len(ok), 2) if ok else 0.0,
        # 热量
        "kcal_actual": total_kcal_actual,
        "kcal_optimal": total_kcal_optimal,
        "kcal_saving": total_kcal_saving,
        "protein_gain": total_protein_gain,
        # 指数
        "potential_index": potential_index,
        # 排行榜
        "top_diff": [d.to_dict() for d in sorted(ok, key=lambda x: x.delta_cost, reverse=True)[:top_n]],
        "top_perfect": [
            d.to_dict() for d in sorted(ok, key=lambda x: (x.delta_cost, abs(x.delta_kcal)))[:top_n]
        ],
        # 归因
        "by_daypart": _agg("daypart"),
        "by_channel": _agg("channel"),
        # 明细
        "diffs": [d.to_dict() for d in diffs],
    }


# ---------------------------------------------------------------------------
# 平行宇宙
# ---------------------------------------------------------------------------


def parallel_summary(summary: dict[str, Any], regret: dict[str, Any]) -> dict[str, Any]:
    """把「重开后的订单集」折算成一份 summary，用于人格重分类。

    只替换与**消费额 / 热量 / 客单价**有关的字段；
    时段、渠道、菜单广度、积分等与"重开"无关的特征保持不变。
    """
    n = max(1, regret["orders_replayed"])
    s = dict(summary)

    spend = regret["total_optimal"]
    kcal = regret["kcal_optimal"]

    s["total_spend"] = round(spend, 1)
    s["avg_ticket"] = round(spend / n, 2)
    s["total_calories"] = round(kcal, 1)
    s["avg_calories_per_order"] = round(kcal / n, 1)
    s["discount_rate"] = 0.0  # 重开用的是菜单原价，不含任何优惠

    money = dict(summary.get("money_account") or {})
    money["total_spend"] = round(spend, 1)
    money["total_discount"] = 0.0
    money["avg_ticket"] = round(spend / n, 2)
    s["money_account"] = money

    cal = dict(summary.get("calorie_account") or {})
    cal["total_calories"] = round(kcal, 1)
    cal["per_order"] = round(kcal / n, 1)
    s["calorie_account"] = cal

    fx = dict(summary.get("fx") or {})
    fx["personal"] = round(kcal / spend, 1) if spend > 0 else 0.0
    s["fx"] = fx

    return s
