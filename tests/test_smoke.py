"""冒烟测试：覆盖差额引擎的核心**性质**、安全边界与三种输出格式。

重点不是比对具体数字，而是验证那些必须成立的不变量：
    ① Δcost ≥ 0（预算上限 = 原单实付，数学上保证）
    ② 重开方案的品类必须覆盖原单品类（结构不变）
    ③ 重开方案的热量 ≤ 原单热量（热量上限 = 原单热量）
    ④ 全程不触碰任何交易类工具
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_diff.collectors import assert_readonly  # noqa: E402
from mcd_diff.menu import load_menu  # noqa: E402
from mcd_diff.metrics import daypart_of, parse_time, summarize  # noqa: E402
from mcd_diff.models import Order  # noqa: E402
from mcd_diff.persona import classify  # noqa: E402
from mcd_diff.regret import (  # noqa: E402
    MODES,
    categories_of_order,
    parallel_summary,
    replay_order,
    replay_orders,
    summarize_regret,
)
from mcd_diff.renderer import render_html, render_json, render_markdown  # noqa: E402
from mcd_diff.solver import Constraints, solve_dual_constraint  # noqa: E402

SAMPLE = ROOT / "examples" / "sample_raw.json"


def _order(items: list[dict], total: float, *, oid: str = "T1", time: str = "2026-03-10T12:30:00+08:00",
           channel: str = "dinein") -> Order:
    return Order.from_dict(
        {
            "order_id": oid,
            "time": time,
            "channel": channel,
            "store": "测试店",
            "total": total,
            "items": items,
        }
    )


BIGMAC_MEAL = [
    {"name": "巨无霸", "qty": 1, "price": 25.5, "calories": 550, "protein": 26},
    {"name": "中薯条", "qty": 1, "price": 13.0, "calories": 230, "protein": 3},
    {"name": "可口可乐(中)", "qty": 1, "price": 10.0, "calories": 150, "protein": 0},
]


class TestMenuAndSolver(unittest.TestCase):
    def setUp(self) -> None:
        self.menu = load_menu()

    def test_menu_loads(self) -> None:
        self.assertGreaterEqual(len(self.menu), 20)
        self.assertTrue(all(m.name and m.category for m in self.menu))

    def test_dual_constraint_respects_limits(self) -> None:
        c = Constraints(budget=35, kcal_cap=800, protein_floor=15)
        sol = solve_dual_constraint(self.menu, c)
        self.assertTrue(sol.feasible)
        self.assertLessEqual(sol.price, 35 + 1e-6)
        self.assertLessEqual(sol.calories, 800 + 1e-6)
        self.assertGreaterEqual(sol.protein, 15 - 1e-6)

    def test_require_categories_is_enforced(self) -> None:
        c = Constraints(
            budget=60,
            kcal_cap=1200,
            protein_floor=10,
            require_categories=frozenset({"主食", "饮料"}),
        )
        sol = solve_dual_constraint(self.menu, c)
        self.assertTrue(sol.feasible)
        cats = {i.category for i in sol.items}
        self.assertTrue({"主食", "饮料"}.issubset(cats), f"品类未覆盖：{cats}")


class TestRegretEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.menu = load_menu()

    def test_delta_cost_never_negative(self) -> None:
        """预算上限 = 原单实付，所以省下的钱不可能为负。"""
        order = _order(BIGMAC_MEAL, 48.5)
        for mode in MODES:
            d = replay_order(order, self.menu, mode=mode)
            if d.feasible:
                self.assertGreaterEqual(d.delta_cost, -1e-9, f"{mode} 出现了负差额")
                self.assertLessEqual(d.optimal_cost, d.actual_cost + 1e-6)

    def test_categories_preserved(self) -> None:
        """重开必须保持点单结构：原单有饮料，重开也得有。"""
        order = _order(BIGMAC_MEAL, 48.5)
        want = categories_of_order(order, self.menu)
        self.assertEqual(want, {"主食", "小食", "饮料"})
        for mode in MODES:
            d = replay_order(order, self.menu, mode=mode)
            if not d.feasible:
                continue
            names = [i.strip() for i in d.optimal_items]
            got = {m.category for m in self.menu if m.name in names}
            self.assertTrue(want.issubset(got), f"{mode} 丢失品类：{want - got}")

    def test_kcal_never_increases(self) -> None:
        """热量上限 = 原单热量，所以重开的热量不会更高。"""
        order = _order(BIGMAC_MEAL, 48.5)
        for mode in MODES:
            d = replay_order(order, self.menu, mode=mode)
            if d.feasible:
                self.assertLessEqual(d.optimal_kcal, d.actual_kcal + 1e-6)

    def test_restaurant_meal_has_saving(self) -> None:
        """巨无霸套餐在省钱向重开下应当有可优化空间。"""
        d = replay_order(_order(BIGMAC_MEAL, 48.5), self.menu, mode="thrift")
        self.assertTrue(d.feasible)
        self.assertGreater(d.delta_cost, 0)
        self.assertTrue(d.changed)

    def test_infeasible_is_marked_not_crashed(self) -> None:
        """约束苛刻到菜单里确实无解时，应标记不可行而不是抛异常。

        注意：由于「按原单折扣系数缩放菜单」的口径对齐，原组合本身一定
        落在约束内 —— 所以真实的不可行只可能来自**物理上矛盾**的约束。
        """
        order = _order(
            [{"name": "巨无霸", "qty": 1, "price": 25.5, "calories": 10, "protein": 30}],
            25.5,
        )
        d = replay_order(order, self.menu, mode="thrift")
        self.assertFalse(d.feasible)
        self.assertEqual(d.delta_cost, 0.0)
        self.assertTrue(d.note)

    def test_empty_order_handled(self) -> None:
        d = replay_order(_order([], 0.0), self.menu)
        self.assertFalse(d.feasible)

    def test_bad_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            replay_order(_order(BIGMAC_MEAL, 48.5), self.menu, mode="nope")


class TestSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.menu = load_menu()
        cls.raw_orders = [
            _order(BIGMAC_MEAL, 48.5, oid="A"),
            _order(BIGMAC_MEAL, 46.0, oid="B"),
            _order(
                [{"name": "吉士汉堡", "qty": 1, "price": 12.5, "calories": 300, "protein": 15}],
                12.5,
                oid="C",
            ),
        ]

    def test_summarize_totals(self) -> None:
        diffs = replay_orders(self.raw_orders, self.menu, mode="thrift")
        r = summarize_regret(diffs, mode="thrift")
        self.assertEqual(r["orders_total"], 3)
        self.assertEqual(r["orders_replayed"] + r["orders_unfeasible"], 3)
        self.assertGreaterEqual(r["total_saving"], 0.0)
        self.assertLessEqual(r["total_optimal"], r["total_actual"] + 1e-6)
        self.assertGreaterEqual(r["potential_index"], 0)
        self.assertLessEqual(r["potential_index"], 100)
        self.assertEqual(len(r["diffs"]), 3)

    def test_attribution_buckets_cover_all(self) -> None:
        diffs = replay_orders(self.raw_orders, self.menu, mode="thrift")
        r = summarize_regret(diffs, mode="thrift")
        self.assertEqual(sum(b["count"] for b in r["by_channel"]), r["orders_replayed"])

    def test_parallel_summary_rewrites_spend(self) -> None:
        raw = json.loads(SAMPLE.read_text(encoding="utf-8")) if SAMPLE.exists() else None
        if raw is None:
            self.skipTest("缺少示例数据")
        from mcd_diff.collectors import MemoryCollector

        data = MemoryCollector(raw).collect()
        summary = summarize(data, self.menu)
        diffs = replay_orders(data.orders, self.menu, mode="thrift")
        regret = summarize_regret(diffs, mode="thrift")
        para = parallel_summary(summary, regret)
        self.assertAlmostEqual(para["total_spend"], regret["total_optimal"], places=1)
        self.assertNotEqual(para["total_spend"], summary["total_spend"])
        p = classify(para)
        self.assertIn("persona", p)


class TestRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SAMPLE.exists():
            raise unittest.SkipTest("缺少示例数据")
        from mcd_diff.collectors import JsonFileCollector

        cls.menu = load_menu()
        data = JsonFileCollector(SAMPLE).collect()
        cls.summary = summarize(data, cls.menu)
        cls.summary["generated_at"] = "2026-10-09 20:30"
        cls.persona = classify(cls.summary)
        diffs = replay_orders(data.orders, cls.menu, mode="thrift")
        cls.regret = summarize_regret(diffs, mode="thrift")
        cls.parallel = classify(parallel_summary(cls.summary, cls.regret))

    def test_html_has_all_sections(self) -> None:
        html = render_html(self.summary, self.persona, self.regret, self.parallel, [])
        for token in ("重开橱窗", "差额账簿", "平行宇宙", "非麦当劳官方产品"):
            self.assertIn(token, html)
        self.assertNotIn("$HERO", html)
        self.assertNotIn("$REPLAY", html)

    def test_markdown_has_diff_block(self) -> None:
        md = render_markdown(self.summary, self.persona, self.regret, self.parallel, [])
        self.assertIn("```diff", md)
        self.assertIn("麦门 Diff", md)

    def test_json_is_parsable(self) -> None:
        js = render_json(self.summary, self.persona, self.regret, self.parallel, [])
        payload = json.loads(js)
        self.assertIn("regret", payload)
        self.assertIn("persona", payload)
        self.assertEqual(payload["regret"]["orders_total"], self.regret["orders_total"])


class TestUtilities(unittest.TestCase):
    def test_parse_time_and_daypart(self) -> None:
        dt = parse_time("2026-03-10T08:30:00+08:00")
        self.assertIsNotNone(dt)
        self.assertEqual(daypart_of(8.5), "breakfast")
        self.assertEqual(daypart_of(22.0), "late")
        self.assertEqual(daypart_of(12.0), "lunch")

    def test_parse_time_bad_input(self) -> None:
        self.assertIsNone(parse_time(""))


class TestSafetyBoundary(unittest.TestCase):
    def test_forbidden_tools_rejected(self) -> None:
        for tool in (
            "create-order",
            "cancel-order",
            "calculate-price",
            "draw-lottery",
            "party-order-create",
            "mall-create-order",
            "auto-bind-coupons",
        ):
            with self.assertRaises(PermissionError):
                assert_readonly(tool)

    def test_readonly_tools_allowed(self) -> None:
        for tool in ("order-list", "query-order", "list-nutrition-foods", "mall-order-list"):
            assert_readonly(tool)  # 不应抛异常


if __name__ == "__main__":
    unittest.main(verbosity=2)
