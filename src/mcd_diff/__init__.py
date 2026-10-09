"""麦门 Diff (mcd-diff)

把你的麦当劳账单，跑一遍 diff。

对历史订单中的每一单，在「该单自身实付金额 ∩ 自身热量」约束内重开一次，
算出你本可以省下多少钱、少吃多少热量 —— 但不做价值判断。

纯标准库实现，离线可跑：

    python -m mcd_diff diff  --input raw.json --out report.html
    python -m mcd_diff solve --budget 35 --kcal 800
"""

from .collectors import JsonFileCollector, McpClient, assert_readonly
from .menu import MenuItem, benchmark_fx, load_menu
from .metrics import summarize
from .models import Account, Coupon, MallOrder, Order, OrderItem, RawData
from .persona import PERSONAS, classify
from .regret import (
    MODES,
    OrderDiff,
    parallel_summary,
    replay_order,
    replay_orders,
    summarize_regret,
)
from .renderer import render_html, render_json, render_markdown
from .solver import Constraints, Solution, compare_three, solve_dual_constraint

__version__ = "1.0.0"

__all__ = [
    "Account",
    "Coupon",
    "MallOrder",
    "Order",
    "OrderItem",
    "RawData",
    "McpClient",
    "JsonFileCollector",
    "assert_readonly",
    "MenuItem",
    "load_menu",
    "benchmark_fx",
    "summarize",
    "classify",
    "PERSONAS",
    "MODES",
    "OrderDiff",
    "replay_order",
    "replay_orders",
    "summarize_regret",
    "parallel_summary",
    "Constraints",
    "Solution",
    "compare_three",
    "solve_dual_constraint",
    "render_html",
    "render_markdown",
    "render_json",
    "__version__",
]
