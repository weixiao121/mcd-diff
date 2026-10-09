"""命令行入口。

两个子命令：

    # 1. 生成《麦门 Diff》重开报告
    python -m mcd_diff diff --input raw.json --out report.html

    # 2. 单次双约束求解（仍是纯本地计算，不产生订单）
    python -m mcd_diff solve --budget 35 --kcal 800

兼容旧用法：不带子命令时默认为 diff。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .collectors import JsonFileCollector
from .menu import load_menu
from .metrics import summarize
from .persona import classify
from .regret import (
    DEFAULT_MODE,
    MODES,
    parallel_summary,
    replay_orders,
    summarize_regret,
)
from .renderer import RENDERERS
from .solver import Constraints, compare_three

SUBCOMMANDS = ("diff", "solve")

#: 报告里并排对照的三条路线
ROUTES = ("thrift", "balanced", "lean")


def _write(text: str, out: str) -> None:
    if out == "-":
        print(text)
        return
    p = Path(out)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def cmd_diff(args: argparse.Namespace) -> int:
    try:
        raw = JsonFileCollector(args.input).collect()
    except FileNotFoundError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    try:
        menu = load_menu(args.menu)
    except FileNotFoundError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    summary = summarize(raw, menu)
    summary["generated_at"] = (
        raw.meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    persona = classify(summary)

    # ---- 重开 ----
    diffs = replay_orders(raw.orders, menu, mode=args.mode)
    regret = summarize_regret(diffs, mode=args.mode, top_n=args.top)
    parallel = classify(parallel_summary(summary, regret))

    # ---- 三条路线对照 ----
    mode_rows = [
        summarize_regret(replay_orders(raw.orders, menu, mode=m), mode=m, top_n=0)
        for m in ROUTES
    ]

    output = RENDERERS[args.format](summary, persona, regret, parallel, mode_rows)
    _write(output, args.out)

    if not args.quiet:
        if args.out != "-":
            print(f"✅ 报告已生成：{args.out}")
        print(f"   🔀 重开 {regret['orders_replayed']} 单 ｜ 路线：{MODES.get(args.mode, args.mode)}")
        print(
            f"   💰 ¥{regret['total_actual']:.0f} → ¥{regret['total_optimal']:.0f}"
            f"   本可以省 ¥{regret['total_saving']:.0f}（{regret['saving_rate'] * 100:.1f}%）"
        )
        print(
            f"   🔥 本可以少 {regret['kcal_saving']:.0f} kcal"
            f" ｜ 🥩 蛋白 {regret['protein_gain']:+.0f} g"
        )
        print(
            f"   ⏪ 有重开空间 {regret['orders_changed']} 单"
            f" ｜ 🎯 已是最优 {regret['orders_perfect']} 单"
            f" ｜ 潜力指数 {regret['potential_index']}/100"
        )
        p2 = parallel["persona"]
        print(
            f"   🌀 平行宇宙：{p2['emoji']} {p2['name']}（{parallel['mcd_index']}）"
            f" vs 现实 {persona['persona']['emoji']} {persona['persona']['name']}"
            f"（{persona['mcd_index']}）"
        )
    return 0


# ---------------------------------------------------------------------------
# solve
# ---------------------------------------------------------------------------


def cmd_solve(args: argparse.Namespace) -> int:
    try:
        menu = load_menu(args.menu)
    except FileNotFoundError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    result = compare_three(
        menu,
        Constraints(
            budget=args.budget,
            kcal_cap=args.kcal,
            protein_floor=args.protein,
            sodium_cap=args.sodium,
        ),
    )

    if args.format == "json":
        import json

        output = json.dumps(
            {
                "constraints": result["constraints"],
                "cheapest": result["cheapest"].to_dict(),
                "lightest": result["lightest"].to_dict(),
                "dual": result["dual"].to_dict(),
                "binding": result["binding"],
                "explain": result["explain"],
            },
            ensure_ascii=False,
            indent=2,
        )
    else:
        c = result["constraints"]
        lines = ["# 麦门双约束求解", ""]
        lines.append(
            f"约束：预算 ≤ {c['budget'] or '不限'} ｜ 热量 ≤ {c['kcal_cap'] or '不限'} kcal"
            f" ｜ 蛋白质 ≥ {c['protein_floor']} g"
            + (f" ｜ 钠 ≤ {c['sodium_cap']} mg" if c.get("sodium_cap") else "")
        )
        lines += ["", "| 方案 | 组合 | 价格 | 热量 | 蛋白 | kcal/¥ |", "|---|---|---:|---:|---:|---:|"]
        for key, label in (
            ("cheapest", "A 省钱优先"),
            ("lightest", "B 热量优先"),
            ("dual", "C 双约束"),
        ):
            sol = result[key]
            combo = "、".join(sol.names) if sol.feasible else sol.note
            lines.append(
                f"| {label} | {combo} | ¥{sol.price:.0f} | {sol.calories:.0f} "
                f"| {sol.protein:.0f} g | {sol.fx:.1f} |"
            )
        lines += ["", "## 求解说明", ""]
        lines += [f"- {t}" for t in result["explain"]]
        output = "\n".join(lines)

    _write(output, args.out)
    if not args.quiet and args.out != "-":
        print(f"✅ 求解完成：{args.out}")
        for t in result["explain"]:
            print(f"   {t}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcd-diff",
        description="麦门 Diff —— 把你的麦当劳账单，跑一遍 diff",
    )
    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("diff", help="生成《麦门 Diff》重开报告")
    d.add_argument("--input", "-i", required=True, help="采集数据 raw.json 的路径")
    d.add_argument("--out", "-o", default="-", help="输出路径；'-' 表示打印到标准输出")
    d.add_argument("--format", "-f", choices=["html", "md", "json"], default="html")
    d.add_argument("--menu", default=None, help="菜单 JSON 路径（默认 data/menu.json）")
    d.add_argument(
        "--mode",
        "-m",
        choices=list(MODES),
        default=DEFAULT_MODE,
        help="重开目标：thrift 省钱向（默认）｜ balanced 扎实向 ｜ lean 轻负担向",
    )
    d.add_argument("--top", "-t", type=int, default=5, help="重开橱窗与排行榜展示条数（默认 5）")
    d.add_argument("--quiet", "-q", action="store_true")

    s = sub.add_parser("solve", help="双约束求解：预算 ∩ 热量内求最优一餐")
    s.add_argument("--budget", type=float, default=None, help="预算上限（元）")
    s.add_argument("--kcal", type=float, default=None, help="热量上限（kcal）")
    s.add_argument("--protein", type=float, default=15.0, help="蛋白质下限（g，默认 15）")
    s.add_argument("--sodium", type=float, default=None, help="钠上限（mg）")
    s.add_argument("--menu", default=None, help="菜单 JSON 路径（默认 data/menu.json）")
    s.add_argument("--out", "-o", default="-", help="输出路径；'-' 表示打印到标准输出")
    s.add_argument("--format", "-f", choices=["md", "json"], default="md")
    s.add_argument("--quiet", "-q", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 兼容旧用法：`--input xxx` 直接当 diff
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["diff"] + argv

    args = build_parser().parse_args(argv)

    if args.command == "solve":
        return cmd_solve(args)
    return cmd_diff(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
