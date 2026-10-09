"""双约束求解器 —— 本项目的功能差异化核心。

与榜单上「省钱类」（单目标：最便宜）和「热量类」（单目标：热量达标）不同，
本模块解的是 **双约束可行域内的最优解**，并把只考虑一半的解摆出来做对照：

    A · 省钱优先  : 热量 ≤ K、蛋白质 ≥ P  →  最小化价格
    B · 热量优先  : 预算 ≤ B、蛋白质 ≥ P  →  最小化热量
    C · 双约束    : 预算 ≤ B 且 热量 ≤ K  →  最大化蛋白质   ← 本产品方案

**算法**：一餐组合 ≤ 4 件，菜单 N 项 → 组合数为 O(N⁴)。全枚举即可得到
**精确最优解**，无需启发式，纯标准库、完全确定性、可复现。

为了让它在大菜单上也够快，做了两件事（都不改变结果，只改变顺序）：

1. **组合表只算一次**。枚举 + 求和全部预计算，缓存在菜单指纹上；
   同一份菜单在整轮重开中只构建一次。
2. **按目标预排序，首个可行即最优**。排序键与平手规则一致，所以
   扫到第一个满足约束的组合就可以立刻返回（通常几百次比较）。

> 关键设计：价格缩放系数做成 `Constraints.price_factor` 而不是真去改菜单里的价格 ——
> **正数缩放不改变排序**，于是同一张组合表对所有订单都成立。
> 这也让"每单的折扣系数"这个业务概念直接落在约束层，比复制菜单更省、也更清楚。

安全性：本模块**纯本地计算**，不调用任何 MCP 交易类工具，不产生任何订单。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

from .menu import MenuItem

#: 一餐最多几件（件数越多组合爆炸；4 件已覆盖绝大多数真实点餐）
MAX_COMBO_SIZE = 4


@dataclass
class Solution:
    """一个候选方案。"""

    label: str = "方案"
    items: list[MenuItem] = field(default_factory=list)
    feasible: bool = True
    note: str = ""

    @property
    def price(self) -> float:
        return round(sum(i.price for i in self.items), 2)

    @property
    def calories(self) -> float:
        return round(sum(i.calories for i in self.items), 1)

    @property
    def protein(self) -> float:
        return round(sum(i.protein for i in self.items), 1)

    @property
    def fat(self) -> float:
        return round(sum(i.fat for i in self.items), 1)

    @property
    def carbs(self) -> float:
        return round(sum(i.carbs for i in self.items), 1)

    @property
    def sodium(self) -> float:
        return round(sum(i.sodium for i in self.items), 1)

    @property
    def fx(self) -> float:
        """本方案的每元热量。"""
        return round(self.calories / self.price, 1) if self.price > 0 else 0.0

    @property
    def names(self) -> list[str]:
        return [i.name for i in self.items]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "feasible": self.feasible,
            "note": self.note,
            "items": [i.name for i in self.items],
            "price": self.price,
            "calories": self.calories,
            "protein": self.protein,
            "fat": self.fat,
            "carbs": self.carbs,
            "sodium": self.sodium,
            "fx": self.fx,
        }


@dataclass
class Constraints:
    """约束条件。None 表示该约束不生效。"""

    budget: float | None = None          # 预算上限（元）
    kcal_cap: float | None = None        # 热量上限（kcal）
    protein_floor: float = 15.0          # 蛋白质下限（g），保证"是一顿饭"而非零食
    sodium_cap: float | None = None      # 钠上限（mg）
    #: 必须覆盖的品类集合（None 表示不限制）。「麦门 Diff」用它保证重开方案
    #: **保持原单的点单结构** —— 原单有饮料，重开就必须也有饮料，
    #: 而不是简单地把饮料删掉换来一个更大的省钱数字。
    require_categories: frozenset[str] | None = None
    #: 必须覆盖的**品类份数**（品类 -> 至少几件）。比 `require_categories` 更强：
    #: 原单是「1 个汉堡 + 2 份小食 + 1 杯饮料」，重开之后也得是这个结构，
    #: 而不只是"每个品类沾一个"。用它才能避免"把两份小食并成一份"式的伪优化。
    require_category_counts: dict[str, int] | None = None
    #: 价格缩放系数（口径对齐用）。订单实付含优惠而菜单是原价，
    #: 用 `实付 ÷ 菜单原价` 缩放后两边才可比。
    #:
    #: 之所以做成系数而不是直接缩放菜单里的价格：**正数缩放不改变排序**，
    #: 所以同一张菜单的"按价格排序"结果对所有订单都成立 ——
    #: 组合表只需预计算一次，逐单重开才能从分钟级降到秒级。
    price_factor: float = 1.0


# ---------------------------------------------------------------------------
# 组合表：一次预计算，全流程复用
# ---------------------------------------------------------------------------

#: 预计算好的组合：(价格, 热量, 蛋白, 钠, 品类元组, 餐品元组)
_Combo = tuple[float, float, float, float, tuple[str, ...], tuple[MenuItem, ...]]

#: 缓存：菜单指纹 -> {目标: 按该目标"最优在前"排好序的组合列表}
_TABLE_CACHE: dict[tuple, dict[str, list[_Combo]]] = {}
_TABLE_CACHE_LIMIT = 8

_OBJECTIVE_SORT: dict[str, str] = {
    "min_price": "price",
    "min_kcal": "kcal",
    "max_protein": "protein",
}


def _menu_fingerprint(menu: list[MenuItem], max_size: int) -> tuple:
    return (
        max_size,
        tuple(
            (i.name, i.price, i.calories, i.protein, i.sodium, i.category) for i in menu
        ),
    )


def _build_tables(menu: list[MenuItem], max_size: int) -> dict[str, list[_Combo]]:
    """枚举全部组合一次，并按三种目标各排一遍（最优在前）。

    排序键刻意与 `score()` 的平手规则一致，这样"第一个满足约束的组合"
    就**恰好是**该目标下的最优解 —— 找到即可立即返回，无需扫完全表。
    """
    usable = [i for i in menu if i.price > 0]
    combos: list[_Combo] = []
    for size in range(1, max_size + 1):
        for items in combinations(usable, size):
            combos.append((
                sum(i.price for i in items),
                sum(i.calories for i in items),
                sum(i.protein for i in items),
                sum(i.sodium for i in items),
                # 保留重复项：品类约束要按「份数」校验，不能只留唯一值
                tuple(sorted(i.category for i in items)),
                items,
            ))

    # score 是"越大越好"，所以按 score 取负后升序排 = 最优在前
    return {
        "min_price": sorted(combos, key=lambda t: (t[0], -t[2], t[3])),
        "min_kcal": sorted(combos, key=lambda t: (t[1], -t[2], t[3])),
        "max_protein": sorted(combos, key=lambda t: (-t[2], t[1], t[3])),
    }


def combo_tables(menu: list[MenuItem], max_size: int = MAX_COMBO_SIZE) -> dict[str, list[_Combo]]:
    """取（或构建）该菜单的组合表。同一张菜单在整轮重开中只算一次。"""
    key = _menu_fingerprint(menu, max_size)
    hit = _TABLE_CACHE.get(key)
    if hit is None:
        if len(_TABLE_CACHE) >= _TABLE_CACHE_LIMIT:
            _TABLE_CACHE.clear()
        hit = _build_tables(menu, max_size)
        _TABLE_CACHE[key] = hit
    return hit


def clear_cache() -> None:
    """清空组合表缓存（测试或多菜单场景用）。"""
    _TABLE_CACHE.clear()


def _ok(combo: _Combo, c: Constraints, *, ignore: set[str] = frozenset()) -> bool:
    """校验约束。`combo` 是预计算的元组；`ignore` 中的约束键会被跳过。"""
    base_price, kcal, prot, na, cats, _items = combo
    if "budget" not in ignore and c.budget is not None:
        if base_price * c.price_factor > c.budget + 1e-9:
            return False
    if "kcal_cap" not in ignore and c.kcal_cap is not None and kcal > c.kcal_cap + 1e-9:
        return False
    if "protein_floor" not in ignore and prot < c.protein_floor - 1e-9:
        return False
    if "sodium_cap" not in ignore and c.sodium_cap is not None and na > c.sodium_cap + 1e-9:
        return False
    if c.require_category_counts:
        for cat, need in c.require_category_counts.items():
            if cats.count(cat) < need:
                return False
    elif c.require_categories and not c.require_categories.issubset(cats):
        return False
    return True


def _search(
    menu: list[MenuItem],
    c: Constraints,
    objective: str,
    *,
    ignore: set[str] = frozenset(),
    max_size: int = MAX_COMBO_SIZE,
) -> Solution | None:
    """在约束内寻找最优组合。

    组合表按目标"最优在前"排好序，因此**第一个满足约束的组合就是最优解**，
    命中即可返回 —— 这是把 74 单重开从分钟级压到秒级的关键。
    """
    if objective not in _OBJECTIVE_SORT:
        raise ValueError(f"未知目标：{objective}")

    for combo in combo_tables(menu, max_size)[objective]:
        if not _ok(combo, c, ignore=ignore):
            continue
        items = combo[5]
        return Solution(items=_apply_factor(items, c.price_factor))
    return None


def _apply_factor(items: tuple[MenuItem, ...], factor: float) -> list[MenuItem]:
    """把价格缩放系数落到最终选中的那几个餐品上（口径对齐）。"""
    if factor >= 0.9999 and factor <= 1.0001:
        return list(items)
    return [replace(i, price=i.price * factor) for i in items]


# ---------------------------------------------------------------------------
# 三种方案
# ---------------------------------------------------------------------------


def solve_dual_constraint(menu: list[MenuItem], c: Constraints) -> Solution:
    """C · 双约束：预算 ∩ 热量内最大化蛋白质（本产品方案）。"""
    sol = _search(menu, c, "max_protein")
    if sol is None:
        return Solution(
            label="双约束方案",
            feasible=False,
            note="在当前约束下没有可行解。建议放宽预算或热量上限。",
        )
    sol.label = "双约束方案"
    return sol


def solve_cheapest(menu: list[MenuItem], c: Constraints) -> Solution:
    """A · 省钱优先：热量 ≤ K 内最便宜（忽略预算）。"""
    sol = _search(menu, c, "min_price", ignore={"budget"})
    if sol is None:
        return Solution(label="省钱优先", feasible=False, note="热量上限内找不到满足蛋白质下限的组合。")
    sol.label = "省钱优先"
    return sol


def solve_lightest(menu: list[MenuItem], c: Constraints) -> Solution:
    """B · 热量优先：预算 ≤ B 内热量最低（忽略热量上限）。"""
    sol = _search(menu, c, "min_kcal", ignore={"kcal_cap"})
    if sol is None:
        return Solution(label="热量优先", feasible=False, note="预算内找不到满足蛋白质下限的组合。")
    sol.label = "热量优先"
    return sol


def compare_three(menu: list[MenuItem], c: Constraints) -> dict:
    """一次给出 A / B / C 三个方案 + 结构化的对照结论。"""
    a = solve_cheapest(menu, c)
    b = solve_lightest(menu, c)
    cc = solve_dual_constraint(menu, c)

    binding: list[str] = []
    if cc.feasible:
        if c.budget is not None and cc.price >= c.budget * 0.95:
            binding.append("预算")
        if c.kcal_cap is not None and cc.calories >= c.kcal_cap * 0.95:
            binding.append("热量")

    return {
        "constraints": {
            "budget": c.budget,
            "kcal_cap": c.kcal_cap,
            "protein_floor": c.protein_floor,
            "sodium_cap": c.sodium_cap,
        },
        "cheapest": a,
        "lightest": b,
        "dual": cc,
        "binding": binding,
        "explain": _explain(a, b, cc, c, binding),
    }


def _explain(a: Solution, b: Solution, c: Solution, cons: Constraints, binding: list[str]) -> list[str]:
    """生成可解释的求解说明。"""
    lines: list[str] = []

    if a.feasible:
        lines.append(
            f"只省钱（{a.label}）：¥{a.price:.0f} / {a.calories:.0f} kcal / 蛋白 {a.protein:.0f}g —— "
            f"价格最低，但没有预算约束，组合往往偏单调。"
        )
    else:
        lines.append(f"只省钱（{a.label}）：不可行 —— {a.note}")

    if b.feasible:
        lines.append(
            f"只控热量（{b.label}）：¥{b.price:.0f} / {b.calories:.0f} kcal / 蛋白 {b.protein:.0f}g —— "
            f"热量最低，但可能又贵又不顶饱。"
        )
    else:
        lines.append(f"只控热量（{b.label}）：不可行 —— {b.note}")

    if c.feasible:
        lines.append(
            f"双约束（{c.label}）：¥{c.price:.0f} / {c.calories:.0f} kcal / 蛋白 {c.protein:.0f}g —— "
            f"在两个上限同时成立的前提下，蛋白质最高的一餐。"
        )
        if binding:
            lines.append(f"紧约束：{'、'.join(binding)}已接近上限，是本次方案的边界条件。")
    else:
        lines.append(f"双约束（{c.label}）：不可行 —— {c.note}")

    return lines
