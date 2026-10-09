"""双约束求解器 —— 本项目的功能差异化核心。

与榜单上「省钱类」（单目标：最便宜）和「热量类」（单目标：热量达标）不同，
本模块解的是 **双约束可行域内的最优解**，并把只考虑一半的解摆出来做对照：

    A · 省钱优先  : 热量 ≤ K、蛋白质 ≥ P  →  最小化价格
    B · 热量优先  : 预算 ≤ B、蛋白质 ≥ P  →  最小化热量
    C · 双约束    : 预算 ≤ B 且 热量 ≤ K  →  最大化蛋白质   ← 本产品方案

**算法**：菜单约 24 项、一餐组合 ≤ 4 件 → 全集仅 12,950 种组合，
全枚举即可得到**精确最优解**，无需启发式，纯标准库、完全确定性、可复现。

安全性：本模块**纯本地计算**，不调用任何 MCP 交易类工具，不产生任何订单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable

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


def _iter_combos(menu: list[MenuItem], max_size: int = MAX_COMBO_SIZE) -> Iterable[tuple[MenuItem, ...]]:
    """枚举 1..max_size 件的所有组合。"""
    usable = [i for i in menu if i.price > 0]
    for size in range(1, max_size + 1):
        yield from combinations(usable, size)


def _ok(items: tuple[MenuItem, ...], c: Constraints, *, ignore: set[str] = frozenset()) -> bool:
    """校验约束。`ignore` 中的约束键会被跳过（用于单目标对照方案）。"""
    price = sum(i.price for i in items)
    kcal = sum(i.calories for i in items)
    prot = sum(i.protein for i in items)
    na = sum(i.sodium for i in items)

    if "budget" not in ignore and c.budget is not None and price > c.budget + 1e-9:
        return False
    if "kcal_cap" not in ignore and c.kcal_cap is not None and kcal > c.kcal_cap + 1e-9:
        return False
    if "protein_floor" not in ignore and prot < c.protein_floor - 1e-9:
        return False
    if "sodium_cap" not in ignore and c.sodium_cap is not None and na > c.sodium_cap + 1e-9:
        return False
    if c.require_categories:
        if not c.require_categories.issubset({i.category for i in items}):
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
    """在约束内按 target 寻找最优组合。"""

    def score(items: tuple[MenuItem, ...]) -> tuple:
        price = sum(i.price for i in items)
        kcal = sum(i.calories for i in items)
        prot = sum(i.protein for i in items)
        na = sum(i.sodium for i in items)
        # 统一为"越大越好"；末位用钠做平手时的次级偏好（越低越好 → 取负）
        if objective == "max_protein":
            return (prot, -kcal, -na)
        if objective == "min_price":
            return (-price, prot, -na)
        if objective == "min_kcal":
            return (-kcal, prot, -na)
        raise ValueError(f"未知目标：{objective}")

    best: tuple[MenuItem, ...] | None = None
    best_score: tuple | None = None

    for combo in _iter_combos(menu, max_size):
        if not _ok(combo, c, ignore=ignore):
            continue
        s = score(combo)
        if best_score is None or s > best_score:
            best, best_score = combo, s

    return Solution(items=list(best)) if best else None


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
