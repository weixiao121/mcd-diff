"""菜单层：加载菜品与营养数据，并计算单品级指标。

真实运行时，菜单来自 MCP 的 `query-meals` + `list-nutrition-foods`；
本模块负责把两者 join 成一个统一的 `MenuItem` 列表，供求解器与四象限分析使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MENU_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "menu.json"


@dataclass
class MenuItem:
    name: str
    category: str = "其他"
    price: float = 0.0
    calories: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0
    sodium: float = 0.0

    @property
    def kcal_per_yuan(self) -> float:
        """每元热量（kcal / ¥）。价格为 0 时返回 0，避免除零。"""
        return (self.calories / self.price) if self.price > 0 else 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MenuItem":
        def _f(v: Any) -> float:
            try:
                return float(v) if v not in (None, "") else 0.0
            except (TypeError, ValueError):
                return 0.0

        return cls(
            name=str(d.get("name") or "").strip(),
            category=str(d.get("category") or "其他"),
            price=_f(d.get("price")),
            calories=_f(d.get("calories") or d.get("kcal")),
            protein=_f(d.get("protein")),
            fat=_f(d.get("fat")),
            carbs=_f(d.get("carbs")),
            sodium=_f(d.get("sodium")),
        )


def load_menu(path: str | Path | None = None) -> list[MenuItem]:
    """加载菜单。默认读取仓库内 `data/menu.json`。"""
    p = Path(path) if path else DEFAULT_MENU_PATH
    if not p.exists():
        raise FileNotFoundError(f"未找到菜单文件：{p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw.get("items") if isinstance(raw, dict) else raw
    return [MenuItem.from_dict(i) for i in (items or []) if isinstance(i, dict)]


def benchmark_fx(menu: list[MenuItem]) -> float:
    """菜单基准汇率：所有单品 kcal/¥ 的中位数（不受优惠影响的"官方刻度"）。"""
    vals = sorted(i.kcal_per_yuan for i in menu if i.price > 0)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
