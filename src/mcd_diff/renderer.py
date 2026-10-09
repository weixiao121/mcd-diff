"""渲染层：把「重开结果 + 平行宇宙」渲染为自包含 HTML / Markdown / JSON。

产物是一个**可双击打开的 HTML 文件**，不依赖网络、不依赖任何 JS 框架。

全篇使用 `diff` 语法表达"重开"：`- 你点的` / `+ 重开的` / `Δ 差额`。
选择 diff 不只是视觉修辞 —— 它让"到底改了什么"变成一眼可见的结构，
也是这个项目名字的来源。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template
from typing import Any, Callable

TEMPLATE_PATH = Path(__file__).parent / "templates" / "diff.html"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _money(v: float) -> str:
    return f"¥{v:,.0f}" if abs(v) >= 100 else f"¥{v:,.2f}".rstrip("0").rstrip(".")


def _num(v: float) -> str:
    return f"{v:,.0f}"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


# ---------------------------------------------------------------------------
# 板块：重开橱窗
# ---------------------------------------------------------------------------


def _delta_text(d: dict[str, Any]) -> str:
    """把差额翻译成一句人话。"""
    delta = d["delta"]
    parts = [f"便宜 {_money(delta['cost'])}"]
    if abs(delta["kcal"]) >= 5:
        parts.append(f"{'少' if delta['kcal'] > 0 else '多'} {_num(abs(delta['kcal']))} kcal")
    if delta["protein"] > 0.5:
        parts.append(f"蛋白 +{delta['protein']:.0f} g")
    else:
        parts.append("蛋白持平")
    return " · ".join(parts)


def _replay_card(d: dict[str, Any], index: int) -> str:
    a, o = d["actual"], d["optimal"]
    note = f'<p class="rp-note">{_e(d["note"])}</p>' if d.get("note") else ""
    return f"""      <article class="replay">
        <div class="rp-head">
          <span class="rp-idx">#{index:02d}</span>
          <span class="rp-date">{_e(d["time"])}</span>
          <span class="rp-store">{_e(d["store"])}</span>
          <span class="rp-save">省 {_money(d["delta"]["cost"])}</span>
        </div>
        <div class="rp-grid">
          <div class="rp-col rp-old">
            <span class="rp-tag">− 你点的</span>
            <p class="rp-items">{" · ".join(_e(x) for x in a["items"]) or "—"}</p>
            <p class="rp-sum">{_money(a["cost"])} · {_num(a["kcal"])} kcal · {a["protein"]:.0f} g</p>
          </div>
          <div class="rp-col rp-new">
            <span class="rp-tag">+ 重开的</span>
            <p class="rp-items">{" · ".join(_e(x) for x in o["items"]) or "—"}</p>
            <p class="rp-sum">{_money(o["cost"])} · {_num(o["kcal"])} kcal · {o["protein"]:.0f} g</p>
          </div>
        </div>
        <p class="rp-delta">Δ {_delta_text(d)}</p>
        {note}
      </article>"""


def _replay_window(regret: dict[str, Any], limit: int = 5) -> str:
    rows = regret["top_diff"][:limit]
    if not rows:
        return '<p class="empty">这一年没有可重开的订单 —— 你已经点得很好了。</p>'
    return "\n".join(_replay_card(d, i + 1) for i, d in enumerate(rows))


# ---------------------------------------------------------------------------
# 板块：差额账簿
# ---------------------------------------------------------------------------


def _ledger_cards(regret: dict[str, Any]) -> str:
    cards = [
        ("可节省空间", _money(regret["total_saving"]), f"占全年支出的 {_pct(regret['saving_rate'])}"),
        ("可减热量", f"{_num(regret['kcal_saving'])} kcal", "在同等约束下折算"),
        ("蛋白净变化", f"+{_num(regret['protein_gain'])} g", "重开后多摄入"),
        ("单均可省", _money(regret["avg_saving_per_order"]), f"{regret['orders_replayed']} 单平均"),
        ("重开潜力", f"{regret['potential_index']}", "/ 100"),
    ]
    return "\n".join(
        f'        <div class="card"><span class="card-num">{v}</span>'
        f'<span class="card-label">{_e(k)}</span>'
        f'<span class="card-sub">{_e(sub)}</span></div>'
        for k, v, sub in cards
    )


def _attribution(rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return ""
    peak = max((r["rate"] for r in rows), default=0.0) or 1.0
    items = []
    for r in rows:
        width = max(2.0, r["rate"] / peak * 100.0)
        items.append(
            f'          <div class="attr-row">'
            f'<span class="attr-key">{_e(r["key"])}</span>'
            f'<span class="attr-bar"><i style="width:{width:.1f}%"></i></span>'
            f'<span class="attr-val">{_money(r["saving"])} · {_pct(r["rate"])}</span>'
            f"</div>"
        )
    return (
        f'        <div class="attr"><h3>{_e(title)}</h3>\n' + "\n".join(items) + "\n        </div>"
    )


# ---------------------------------------------------------------------------
# 板块：三条路线
# ---------------------------------------------------------------------------


def _mode_rows(mode_rows: list[dict[str, Any]], default_mode: str = "thrift") -> str:
    labels = {"thrift": "省钱向", "balanced": "扎实向", "lean": "轻负担向"}
    descs = {
        "thrift": "同预算同热量内，价格压到最低",
        "balanced": "同预算同热量内，蛋白质拉到最高",
        "lean": "同预算同热量内，热量压到最低",
    }
    out = []
    for r in mode_rows:
        key = r["mode"]
        cls = ' class="is-default"' if key == default_mode else ""
        out.append(
            f"          <tr{cls}>"
            f'<td><b>{labels.get(key, key)}</b><span class="mdesc">{descs.get(key, "")}</span></td>'
            f'<td class="num strong">{_money(r["total_saving"])}</td>'
            f'<td class="num">{_num(r["kcal_saving"])} kcal</td>'
            f'<td class="num">{r["protein_gain"]:+.0f} g</td>'
            f'<td class="num">{r["potential_index"]}</td>'
            "</tr>"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 板块：排行榜
# ---------------------------------------------------------------------------


def _ranking(rows: list[dict[str, Any]], kind: str) -> str:
    if not rows:
        return '<p class="empty">—</p>'
    out = []
    for d in rows:
        a = d["actual"]
        combo = " · ".join(_e(x) for x in a["items"])
        if kind == "top":
            right = f'<span class="rk-delta minus">省 {_money(d["delta"]["cost"])}</span>'
        else:
            right = '<span class="rk-delta plus">已是最优</span>'
        out.append(
            f'          <li><span class="rk-date">{_e(d["time"][:10])}</span>'
            f'<span class="rk-combo">{combo}</span>'
            f'<span class="rk-cost">{_money(a["cost"])}</span>{right}</li>'
        )
    return '        <ul class="ranking">\n' + "\n".join(out) + "\n        </ul>"


# ---------------------------------------------------------------------------
# 板块：平行宇宙
# ---------------------------------------------------------------------------


def _pl_metrics(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f'              <li><i>{_e(k)}</i><b>{v}</b></li>' for k, v in rows)


def _parallel_block(
    summary: dict[str, Any],
    persona: dict[str, Any],
    regret: dict[str, Any],
    para_persona: dict[str, Any],
) -> str:
    n = max(1, regret["orders_replayed"])
    real_rows = [
        ("年支出", _money(regret["total_actual"])),
        ("客单价", _money(regret["total_actual"] / n)),
        ("热量汇率", f"{(summary.get('fx') or {}).get('personal', 0.0):.1f}"),
    ]
    para_rows = [
        ("年支出", _money(regret["total_optimal"])),
        ("客单价", _money(regret["total_optimal"] / n)),
        (
            "热量汇率",
            f"{regret['kcal_optimal'] / regret['total_optimal']:.1f}"
            if regret["total_optimal"]
            else "—",
        ),
    ]

    rp, pp = persona["persona"], para_persona["persona"]
    avg_ticket = (summary.get("avg_ticket") or 0) or (regret["total_actual"] / n)
    meals = (regret["total_saving"] / avg_ticket) if avg_ticket else 0.0

    return f"""        <div class="parallel">
          <div class="pl-col pl-real">
            <span class="pl-tag">现实宇宙</span>
            <span class="pl-emoji">{_e(rp["emoji"])}</span>
            <span class="pl-name">{_e(rp["name"])}</span>
            <span class="pl-index">麦门指数 {persona["mcd_index"]}</span>
            <ul class="pl-metrics">
{_pl_metrics(real_rows)}
            </ul>
          </div>
          <div class="pl-mid">
            <span class="pl-vs">重开之后</span>
            <span class="pl-gap up">省下 {_money(regret["total_saving"])}</span>
          </div>
          <div class="pl-col pl-para">
            <span class="pl-tag">平行宇宙</span>
            <span class="pl-emoji">{_e(pp["emoji"])}</span>
            <span class="pl-name">{_e(pp["name"])}</span>
            <span class="pl-index">麦门指数 {para_persona["mcd_index"]}</span>
            <ul class="pl-metrics">
{_pl_metrics(para_rows)}
            </ul>
          </div>
        </div>
        <p class="pl-foot">你少花的 {_money(regret["total_saving"])}，相当于 <b>{meals:.1f} 顿</b>麦当劳（按你的平均客单价计）。</p>"""


def _badges(badges: list[dict[str, str]]) -> str:
    if not badges:
        return ""
    return "\n".join(
        f'          <div class="badge"><span class="bd-emoji">{_e(b["emoji"])}</span>'
        f'<span class="bd-body"><b>{_e(b["name"])}</b><i>{_e(b["desc"])}</i></span></div>'
        for b in badges
    )


def diff_badges(regret: dict[str, Any]) -> list[dict[str, str]]:
    """差额语义的徽章（与单纯的人格徽章不同）。"""
    out: list[dict[str, str]] = []
    if regret["total_saving"] >= 200:
        out.append({"emoji": "🪙", "name": "重开大户", "desc": f"一年可省 {_money(regret['total_saving'])}"})
    if regret["kcal_saving"] >= 3000:
        out.append({"emoji": "🔥", "name": "热量回收师", "desc": f"可少摄入 {_num(regret['kcal_saving'])} kcal"})
    if regret["orders_perfect"] >= regret["orders_replayed"] * 0.4:
        out.append({"emoji": "🎯", "name": "本来就会点", "desc": f"{regret['orders_perfect']} 单已是最优"})
    by_ch = {r["key"]: r for r in regret["by_channel"]}
    if by_ch.get("delivery", {}).get("rate", 0) >= 0.08:
        out.append({"emoji": "🛵", "name": "外卖税", "desc": "外卖单的可优化空间明显偏高"})
    by_dp = {r["key"]: r for r in regret["by_daypart"]}
    if by_dp.get("late", {}).get("rate", 0) >= 0.12:
        out.append({"emoji": "🌙", "name": "深夜溢价", "desc": "深夜单的可优化空间最高"})
    top = regret["top_diff"][0] if regret["top_diff"] else None
    if top and top["delta"]["cost"] >= 15:
        out.append({"emoji": "💥", "name": "单笔之最", "desc": f"一单可省 {_money(top['delta']['cost'])}"})
    return out


# ---------------------------------------------------------------------------
# 渲染入口
# ---------------------------------------------------------------------------


def render_html(
    summary: dict[str, Any],
    persona: dict[str, Any],
    regret: dict[str, Any],
    parallel: dict[str, Any],
    mode_rows: list[dict[str, Any]] | None = None,
) -> str:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"未找到模板：{TEMPLATE_PATH}")

    modes = mode_rows or []
    win = summary.get("window", {})
    vals = {
        "GENERATED_AT": _e(summary.get("generated_at", "—")),
        "ORDERS": _num(regret["orders_replayed"]),
        "WINDOW": _e(f"{win.get('start', '—')} ~ {win.get('end', '—')}"),
        "HERO_SAVING": _money(regret["total_saving"]),
        "HERO_OPTIMAL": _money(regret["total_optimal"]),
        "HERO_POTENTIAL": f"{regret['potential_index']}",
        "HERO_KCAL": _num(regret["kcal_saving"]),
        "HERO_RATE": _pct(regret["saving_rate"]),
        "HERO_ACTUAL": _money(regret["total_actual"]),
        "REPLAY_WINDOW": _replay_window(regret, 5),
        "LEDGER_CARDS": _ledger_cards(regret),
        "ATTR_DAYPART": _attribution(regret["by_daypart"], "按时段"),
        "ATTR_CHANNEL": _attribution(regret["by_channel"], "按渠道"),
        "MODE_ROWS": _mode_rows(modes),
        "TOP_RANKING": _ranking(regret["top_diff"][:5], "top"),
        "PERFECT_RANKING": _ranking(regret["top_perfect"][:5], "perfect"),
        "PARALLEL": _parallel_block(summary, persona, regret, parallel),
        "BADGES": _badges(diff_badges(regret)),
        "SCALE": _e(
            f"{regret['orders_replayed']} 单中 {regret['orders_changed']} 单存在重开空间"
            f"（{_pct(regret['changed_rate'])}）"
        ),
    }
    return Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(vals)


def render_markdown(
    summary: dict[str, Any],
    persona: dict[str, Any],
    regret: dict[str, Any],
    parallel: dict[str, Any],
    mode_rows: list[dict[str, Any]] | None = None,
) -> str:
    win = summary.get("window", {})
    L: list[str] = []
    L.append("# 🔀 麦门 Diff · 你的订单重开报告")
    L.append("")
    L.append(
        f"> {summary.get('generated_at', '—')} ｜ 统计窗口 "
        f"{win.get('start', '—')} ~ {win.get('end', '—')}"
    )
    L.append("")
    L.append(f"我把在麦当劳的 **{regret['orders_replayed']} 单**，重新点了一遍。")
    L.append("")
    L.append("```diff")
    L.append(f"- 实际支出      {_money(regret['total_actual'])}")
    L.append(f"+ 重开之后      {_money(regret['total_optimal'])}")
    L.append(f"  本可以省下    {_money(regret['total_saving'])}（{_pct(regret['saving_rate'])}）")
    L.append(f"  本可以少吃    {_num(regret['kcal_saving'])} kcal")
    L.append("```")
    L.append("")
    L.append("**但我没有 —— 因为好吃。** 🍟")
    L.append("")
    L.append("---")
    L.append("")

    L.append("## ⏪ 重开橱窗")
    L.append("")
    for i, d in enumerate(regret["top_diff"][:5], 1):
        a, o = d["actual"], d["optimal"]
        L.append(f"### #{i:02d} {d['time']} · {d['store']}")
        L.append("")
        L.append("```diff")
        L.append(f"- {' · '.join(a['items'])}")
        L.append(f"+ {' · '.join(o['items'])}")
        L.append(f"  {_money(a['cost'])} → {_money(o['cost'])}   {_delta_text(d)}")
        L.append("```")
        if d.get("note"):
            L.append(f"> {d['note']}")
        L.append("")

    L.append("## 💸 差额账簿")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append(f"| 可节省空间 | **{_money(regret['total_saving'])}**（占全年 {_pct(regret['saving_rate'])}） |")
    L.append(f"| 可减热量 | {_num(regret['kcal_saving'])} kcal |")
    L.append(f"| 蛋白净变化 | +{_num(regret['protein_gain'])} g |")
    L.append(f"| 单均可省 | {_money(regret['avg_saving_per_order'])} |")
    L.append(f"| 重开潜力 | {regret['potential_index']} / 100 |")
    L.append(f"| 有重开空间的单 | {regret['orders_changed']} / {regret['orders_replayed']} |")
    L.append("")

    if mode_rows:
        labels = {"thrift": "省钱向", "balanced": "扎实向", "lean": "轻负担向"}
        L.append("## 🧭 三条重开路线")
        L.append("")
        L.append("| 路线 | 可省金额 | 可减热量 | 蛋白净变化 | 潜力指数 |")
        L.append("|---|---:|---:|---:|---:|")
        for r in mode_rows:
            L.append(
                f"| {labels.get(r['mode'], r['mode'])} | **{_money(r['total_saving'])}** "
                f"| {_num(r['kcal_saving'])} kcal | {r['protein_gain']:+.0f} g | {r['potential_index']} |"
            )
        L.append("")

    L.append("## 🏆 最值得重开的 5 单")
    L.append("")
    L.append("| # | 日期 | 你点的 | 实付 | 可省 |")
    L.append("|---:|---|---|---:|---:|")
    for i, d in enumerate(regret["top_diff"][:5], 1):
        L.append(
            f"| {i} | {d['time'][:10]} | {' · '.join(d['actual']['items'])} "
            f"| {_money(d['actual']['cost'])} | **{_money(d['delta']['cost'])}** |"
        )
    L.append("")
    L.append("## ✨ 点得最漂亮的 5 单")
    L.append("")
    for d in regret["top_perfect"][:5]:
        L.append(f"- {d['time'][:10]} · {' · '.join(d['actual']['items'])}（{_money(d['actual']['cost'])}）")
    L.append("")

    L.append("## 🌀 平行宇宙")
    L.append("")
    n = max(1, regret["orders_replayed"])
    L.append("| | 现实宇宙 | 平行宇宙 |")
    L.append("|---|---|---|")
    L.append(
        f"| 人格 | {persona['persona']['emoji']} {persona['persona']['name']}"
        f" | {parallel['persona']['emoji']} {parallel['persona']['name']} |"
    )
    L.append(f"| 麦门指数 | {persona['mcd_index']} | {parallel['mcd_index']} |")
    L.append(
        f"| 年支出 | {_money(regret['total_actual'])} | **{_money(regret['total_optimal'])}** |"
    )
    L.append(
        f"| 客单价 | {_money(regret['total_actual'] / n)} | {_money(regret['total_optimal'] / n)} |"
    )
    L.append("")
    avg_ticket = (summary.get("avg_ticket") or 0) or (regret["total_actual"] / n)
    if avg_ticket:
        L.append(
            f"你少花的 {_money(regret['total_saving'])}，"
            f"相当于 **{regret['total_saving'] / avg_ticket:.1f} 顿**麦当劳（按你的平均客单价计）。"
        )
        L.append("")

    badges = diff_badges(regret)
    if badges:
        L.append("## 🏅 徽章")
        L.append("")
        for b in badges:
            L.append(f"- {b['emoji']} **{b['name']}** —— {b['desc']}")
        L.append("")

    L.append("---")
    L.append("")
    L.append(
        "> ⚠️ 本报告为**机械换算与陈述**，不构成任何消费、营养或健康建议。"
        "所有差额均为在「该单自身实付金额 ∩ 自身热量」约束下的**同条件更优解**，"
        "且已按该单原有优惠力度折算。"
    )
    L.append(">")
    L.append("> 本项目为麦当劳程序员创意开发大赛参赛作品，**非麦当劳官方产品**。")
    return "\n".join(L)


def render_json(
    summary: dict[str, Any],
    persona: dict[str, Any],
    regret: dict[str, Any],
    parallel: dict[str, Any],
    mode_rows: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        "generated_at": summary.get("generated_at", "—"),
        "window": summary.get("window", {}),
        "regret": regret,
        "persona": {
            "real": {"name": persona["persona"]["name"], "mcd_index": persona["mcd_index"]},
            "parallel": {"name": parallel["persona"]["name"], "mcd_index": parallel["mcd_index"]},
        },
        "modes": mode_rows or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


RENDERERS: dict[str, Callable[..., str]] = {
    "html": render_html,
    "md": render_markdown,
    "json": render_json,
}
