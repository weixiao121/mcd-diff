"""采集层 CLI —— 直接对接麦当劳 MCP Server，把原始数据归一化落盘。

    python scripts/collect.py --out .mcd-diff/raw.json --menu .mcd-diff/menu.json

设计要点
--------
1. **零第三方依赖**：只用标准库（urllib）实现 Streamable HTTP 的 MCP 客户端，
   所以 `requirements.txt` 可以一直保持为空。
2. **只读边界**：调用前一律过 `mcd_diff.collectors.assert_readonly()`；
   交易类工具（create-order / cancel-order / …）直接抛 PermissionError。
3. **采集与计算分离**：本脚本只负责"取数 + 落盘"，
   之后所有统计、求解、渲染都由本地计算层完成（零网络）。
4. **失败降级**：辅助工具（券 / 商城 / 活动）失败不阻断主流程，仅记录警告。

Token 来源（按优先级）：`--token` > 环境变量 `MCD_MCP_TOKEN` > WorkBuddy 的 `~/.workbuddy/mcp.json`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_diff.collectors import assert_readonly  # noqa: E402
from mcd_diff.metrics import _norm_name  # noqa: E402

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2024-11-05"


# ---------------------------------------------------------------------------
# 极简 MCP 客户端（Streamable HTTP）
# ---------------------------------------------------------------------------


class McpError(RuntimeError):
    pass


class McpHttpClient:
    """最小可用的 MCP 客户端：initialize → tools/call。"""

    def __init__(self, url: str, token: str, *, timeout: int = 60) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self._id = 0
        self._session: str | None = None
        self.server_info: dict[str, Any] = {}

    # -- 底层 -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str]]:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
                hdrs = {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 401:
                raise McpError(
                    "HTTP 401 —— Token 无效或已过期。请到 https://open.mcd.cn/mcp "
                    "重新激活并更新 Token。"
                ) from exc
            if exc.code == 429:
                raise McpError("HTTP 429 —— 触发限流（600 次/分钟）。请降低调用频率。") from exc
            raise McpError(f"HTTP {exc.code} —— {detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"网络错误：{exc.reason}") from exc
        return _parse_body(body), hdrs

    # -- 协议 -------------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        data, hdrs = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-diff", "version": "1.0.0"},
            },
        })
        if not data or "result" not in data:
            raise McpError(f"initialize 失败：{str(data)[:200]}")
        self._session = hdrs.get("mcp-session-id")
        self.server_info = data["result"].get("serverInfo", {})
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            pass  # 通知失败不影响后续调用
        return self.server_info

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用一个 Tool，返回其 payload（已从响应包装中解出 JSON）。"""
        assert_readonly(name)  # 只读边界：交易类工具在此被拦截
        self._id += 1
        data, _ = self._post({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if not data:
            raise McpError(f"{name} 返回空响应")
        if "error" in data:
            raise McpError(f"{name} JSON-RPC 错误：{str(data['error'])[:200]}")
        result = data.get("result", {})
        content = result.get("content") or []
        text = content[0].get("text", "") if content else ""
        return _extract_payload(text)


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


def _parse_body(body: str) -> dict[str, Any] | None:
    """兼容裸 JSON 与 SSE 两种响应体。"""
    if not body.strip():
        return None
    if body.lstrip().startswith(("{", "[")):
        return json.loads(body)
    out = None
    for line in body.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                out = json.loads(chunk)
    return out


def _extract_payload(text: str) -> Any:
    """从 Tool 返回的 markdown 包装里解出真实 payload。

    MCP 的返回形如「## Response Structure ... ## Original Response\\n{json}」，
    也有的直接是 JSON。这里统一取 `## Original Response` 之后的 JSON，
    否则回退到「在全文中找出第一个可解析的 JSON 对象」。
    """
    if not text:
        return None
    marker = "## Original Response"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 回退：扫描第一个平衡的 JSON 对象
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None
    return None


_NUTRITION_HEADER = re.compile(r"\[(\d+)\]\{([^}]*)\}:")


def parse_nutrition(payload: Any) -> dict[str, dict[str, float]]:
    """解析 `list-nutrition-foods` 的表格字符串。

    原始 data 形如::

        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
          ...

    返回 ``{归一化名称: {calories, protein, fat, carbs, sodium, calcium}}``。
    """
    raw = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(raw, str):
        return {}
    m = _NUTRITION_HEADER.search(raw)
    if not m:
        return {}
    cols = [c.strip() for c in m.group(2).split(",")]
    body = raw[m.end():]

    idx = {c: i for i, c in enumerate(cols)}
    table: dict[str, dict[str, float]] = {}
    for line in body.splitlines():
        line = line.strip().rstrip("\\n").rstrip('"').strip()
        if not line or "," not in line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(cols):
            continue
        name = parts[idx["productName"]]
        if not name or name == "null":
            continue

        def num(col: str) -> float:
            try:
                return float(parts[idx[col]])
            except (ValueError, KeyError, IndexError):
                return 0.0

        table[_strict_norm(name)] = {
            "calories": num("energyKcal"),
            "protein": num("protein"),
            "fat": num("fat"),
            "carbs": num("carbohydrate"),
            "sodium": num("sodium"),
            "calcium": num("calcium"),
        }
    return table


_NUTRITION_ALIASES: tuple[tuple[str, str], ...] = (
    ("(中)", "中杯"), ("（中）", "中杯"), ("(大)", "大杯"), ("（大）", "大杯"),
    ("(小)", "小杯"), ("（小）", "小杯"),
)

#: 套餐 / 多人餐标记。这些**不是单品**，不能直接送进求解器。
_COMBO_PAT = re.compile(
    r"套餐|三件套|四件套|双人餐|多人餐|分享餐|拼盘|随心拼|随心配|组合|家庭餐|欢享"
)

#: 周边商品标记（不是餐品，营养表里自然没有）。
_MERCH_PAT = re.compile(r"帽|玩具|公仔|盲盒|徽章|挂件|抱枕|手办|摆件|马克杯|帆布袋")


def _strict_norm(name: str) -> str:
    """比 `_norm_name` 更严格的归一化：**保留「套餐」二字**。

    `_norm_name` 会剥掉「套餐」，于是 `吉士汉堡包套餐` 会和单品的
    `吉士汉堡包` 撞名 —— 拿**套餐价**配了**单品营养**。
    这里用严格键来防止这种错配。
    """
    return name.replace("（", "(").replace("）", ")").replace(" ", "").strip().lower()


def kind_of(name: str) -> str:
    """判定菜单条目的类型：single（单品）/ combo（套餐）/ merch（周边）。"""
    if _MERCH_PAT.search(name):
        return "merch"
    if _COMBO_PAT.search(name):
        return "combo"
    return "single"


def _lookup_nutrition(name: str, table: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """按名称找营养。

    匹配顺序：严格键 → 别名键 → 去括号主体 → 宽松包含。
    **套餐条目只接受严格键匹配**，避免"套餐价配单品营养"。
    """
    strict = _strict_norm(name)
    if strict in table:
        return table[strict]
    if kind_of(name) != "single":
        return None  # 套餐/周边：不做宽松匹配
    key = _norm_name(name)
    if key in table:
        return table[key]
    for src, dst in _NUTRITION_ALIASES:
        if src in name:
            alt = _strict_norm(name.replace(src, dst))
            if alt in table:
                return table[alt]
    bare = _strict_norm(re.sub(r"[（(][^）)]*[）)]", "", name))
    if bare and bare in table:
        return table[bare]
    for k, v in table.items():
        if bare and (bare in k or k in bare) and abs(len(k) - len(bare)) <= 2:
            return v
    return None


def parse_menu(payload: Any, nutrition: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """把 `query-meals` + `list-nutrition-foods` join 成菜单条目列表。"""
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    meals: dict[str, Any] = data.get("meals") or {}
    categories: list[dict[str, Any]] = data.get("categories") or []

    # code -> 分类名
    cat_of: dict[str, str] = {}
    for c in categories:
        cname = (c.get("name") or "其他").replace("\n", "").strip()
        for m in c.get("meals") or []:
            code = str(m.get("code") or "")
            if code:
                cat_of.setdefault(code, cname)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, m in meals.items():
        name = (m.get("name") or "").replace("\n", "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        price = _to_float(m.get("currentPrice")) or _to_float(m.get("originalPrice"))
        nut = _lookup_nutrition(name, nutrition) or {}
        items.append({
            "name": name,
            "code": str(code),
            "kind": kind_of(name),
            "category": cat_of.get(str(code), "其他"),
            "price": round(price, 2),
            "list_price": round(_to_float(m.get("originalPrice")), 2),
            "calories": nut.get("calories", 0.0),
            "protein": nut.get("protein", 0.0),
            "fat": nut.get("fat", 0.0),
            "carbs": nut.get("carbs", 0.0),
            "sodium": nut.get("sodium", 0.0),
        })
    items.sort(key=lambda x: (x["kind"] != "single", -x["price"], x["name"]))
    return items


_CHANNEL_BY_BETYPE = {"1": "dinein", "2": "delivery", "5": "mcdrive", "6": "party"}


def parse_orders(list_payload: Any, details: dict[str, Any],
                 nutrition: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """把 `order-list` + `query-order` 归一化为内部订单结构。"""
    data = list_payload.get("data") if isinstance(list_payload, dict) else {}
    rows = (data or {}).get("list") or []

    orders: list[dict[str, Any]] = []
    for row in rows:
        oid = str(row.get("orderId") or "")
        det = (details.get(oid) or {}).get("data") or {}

        betype = str(row.get("beType") or det.get("beType") or "1")
        channel = _CHANNEL_BY_BETYPE.get(betype, "dinein")
        take_way = str(det.get("takeWay") or "")
        if channel == "dinein" and "外带" in take_way:
            channel = "pickup"

        # 实付金额：详情优先，其次列表。**即使为 "0" 也算明确给出** ——
        # 0 元单（积分/活动全额抵扣）是事实，不能被菜单价兜底掉。
        raw_total = det.get("realTotalAmount")
        if raw_total in (None, ""):
            raw_total = row.get("realTotalAmount")
        total_reported = raw_total not in (None, "")
        total = _to_float(raw_total)
        discount = _to_float(det.get("totalDiscountAmount"))

        detail_products = det.get("orderProductList") or row.get("orderProductList") or []
        items: list[dict[str, Any]] = []
        for p in detail_products:
            qty = int(_to_float(p.get("quantity"), 1) or 1)
            combos = p.get("comboItemList") or []
            if combos:
                # 套餐 → 拆成子项；子项价格按菜单原价回填（后由差额引擎的口径对齐处理）
                for sub in combos:
                    sname = (sub.get("itemName") or sub.get("name") or "").strip()
                    if not sname:
                        continue
                    items.append(_mk_item(sname, int(_to_float(sub.get("itemQuantity"), 1) or 1),
                                         0.0, nutrition))
            else:
                pname = (p.get("productName") or p.get("name") or "").strip()
                if not pname:
                    continue
                items.append(_mk_item(pname, qty, _to_float(p.get("price")), nutrition))

        orders.append({
            "order_id": oid,
            "time": str(row.get("createTime") or det.get("createTime") or ""),
            "channel": channel,
            "store": str(row.get("storeName") or det.get("storeName") or ""),
            "store_code": str(row.get("storeCode") or ""),
            "status": str(row.get("orderStatus") or det.get("orderStatus") or ""),
            "total": round(total, 2),
            "total_reported": total_reported,
            "discount": round(discount, 2),
            "items": items,
        })

    orders.sort(key=lambda o: o["time"], reverse=True)
    return orders


def _mk_item(name: str, qty: int, price: float,
             nutrition: dict[str, dict[str, float]]) -> dict[str, Any]:
    nut = _lookup_nutrition(name, nutrition) or {}
    return {
        "name": name,
        "qty": max(1, qty),
        "price": round(price, 2),
        **{k: nut.get(k, 0.0) for k in ("calories", "protein", "fat", "carbs", "sodium")},
    }


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _resolve_token(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    env = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if env:
        return env
    cfg = Path.home() / ".workbuddy" / "mcp.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            auth = data["mcpServers"]["mcd-mcp"]["headers"]["Authorization"]
            return str(auth).replace("Bearer", "").strip()
        except Exception:  # noqa: BLE001
            pass
    raise SystemExit(
        "未找到 MCP Token。请用 --token 传入，或设置环境变量 MCD_MCP_TOKEN，\n"
        "或先按 SKILL.md 在 WorkBuddy 的 ~/.workbuddy/mcp.json 里配置 mcd-mcp。"
    )


def collect(args: argparse.Namespace) -> int:
    token = _resolve_token(args.token)
    client = McpHttpClient(args.url, token, timeout=args.timeout)

    info = client.initialize()
    print(f"✅ 已连接 MCP：{info.get('name')} v{info.get('version')}")

    warn: list[str] = []

    def soft(tool: str, fn, default):
        """辅助工具：失败只记警告，不中断主流程。"""
        try:
            return fn()
        except McpError as exc:
            warn.append(f"{tool}: {exc}")
            print(f"   ⚠️  {tool} 失败（已降级）：{exc}")
            return default

    # 1. 时间基准
    now_payload = soft("now-time-info", lambda: client.call_tool("now-time-info"), {})
    now_str = _dig(now_payload, "data", "datetime") or _dig(now_payload, "data", "date")
    print(f"   报告基准时间：{now_str or '(未知)'}")

    # 2. 订单主数据
    print("   拉取 order-list …")
    list_payload = client.call_tool("order-list")
    rows = ((list_payload or {}).get("data") or {}).get("list") or []
    print(f"   → {len(rows)} 笔订单")

    # 3. 逐单详情（含商品与实付）
    details: dict[str, Any] = {}
    for i, row in enumerate(rows, 1):
        oid = str(row.get("orderId") or "")
        if not oid:
            continue
        try:
            details[oid] = client.call_tool("query-order", {"orderId": oid})
        except McpError as exc:
            warn.append(f"query-order({oid}): {exc}")
        if i % 10 == 0:
            print(f"   query-order 进度 {i}/{len(rows)}")

    # 4. 营养表（只拉一次）
    print("   拉取 list-nutrition-foods …")
    nutrition = parse_nutrition(client.call_tool("list-nutrition-foods"))
    print(f"   → {len(nutrition)} 项营养数据")

    # 5. 菜单（需要门店编码，取自订单）
    #
    # ⚠️ 菜单是**随时段变化**的：晚上拉不到早餐品项、早上拉不到正餐品项。
    # 所以对同一门店按多个时段各拉一次，合并去重 —— 这样得到的才是完整菜单。
    menu_items: list[dict[str, Any]] = []
    store_codes: list[str] = []
    for o in rows:
        sc = str(o.get("storeCode") or "")
        if sc and sc not in store_codes:
            store_codes.append(sc)

    base_date = (now_str or "")[:10] or ""
    probe_times: list[str | None] = [None]  # None = 当前时刻
    if args.menu_times:
        try:
            next_day = (
                date.fromisoformat(base_date) + timedelta(days=1)
            ).isoformat() if base_date else ""
        except ValueError:
            next_day = ""
        for t in args.menu_times.split(","):
            t = t.strip()
            if t and next_day:
                probe_times.append(f"{next_day} {t}")

    for sc in store_codes[: args.max_stores]:
        betype = 1
        for o in rows:
            if str(o.get("storeCode")) == sc:
                betype = int(_to_float(o.get("beType"), 1) or 1)
                break

        merged: dict[str, dict[str, Any]] = {}
        for rd in probe_times:
            params: dict[str, Any] = {"storeCode": sc, "orderType": 1, "beType": betype}
            if rd:
                params["reservationDate"] = rd
            label = rd or "当前"
            try:
                got = parse_menu(client.call_tool("query-meals", params), nutrition)
            except McpError as exc:
                warn.append(f"query-meals({sc},{label}): {exc}")
                print(f"   ⚠️  query-meals({sc}, {label}) 失败：{exc}")
                continue
            added = 0
            for it in got:
                key = _strict_norm(it["name"])
                prev = merged.get(key)
                # 同一餐品在不同时段出现时，保留**信息更全**的那条
                if prev is None or (it["calories"] > 0 and prev["calories"] <= 0):
                    merged[key] = it
                    added += 1
            print(f"   query-meals({label}) → {len(got)} 项（新增 {added}）")

        if merged:
            menu_items = sorted(
                merged.values(), key=lambda x: (x["kind"] != "single", -x["price"], x["name"])
            )
            print(f"   → 合并后共 {len(menu_items)} 项")
            break

    # 6. 辅助数据
    account_payload = soft("query-my-account", lambda: client.call_tool("query-my-account"), {})
    coupons_payload = soft(
        "query-my-coupons",
        lambda: client.call_tool("query-my-coupons", {"page": "1", "pageSize": "200"}), {},
    )
    mall_payload = soft(
        "mall-order-list", lambda: client.call_tool("mall-order-list", {"size": 10}), {}
    )
    soft("campaign-calendar", lambda: client.call_tool("campaign-calendar"), {})

    orders = parse_orders(list_payload, details, nutrition)

    raw = {
        "meta": {
            "generated_at": (now_str or "")[:19].replace("T", " ") or None,
            "source": "mcd-mcp",
            "server": info,
            "window_days": args.window_days,
            "order_count": len(orders),
            "replayable": sum(1 for o in orders if o["total"] > 0),
            "notes": warn,
        },
        "account": _parse_account(account_payload),
        "orders": orders,
        "coupons": _parse_coupons(coupons_payload),
        "mall_orders": _parse_mall(mall_payload),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已写入 {out}（{len(orders)} 单，其中 {raw['meta']['replayable']} 单可重开）")

    if menu_items:
        # 求解器只吃「单品」：套餐是求解器自己拼出来的，周边不是餐品。
        singles = [i for i in menu_items if i["kind"] == "single" and i["calories"] > 0]
        combos = [i for i in menu_items if i["kind"] == "combo"]
        merch = [i for i in menu_items if i["kind"] == "merch"]
        mpath = Path(args.menu)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(
            json.dumps(
                {
                    "_note": (
                        "由 scripts/collect.py 从 query-meals + list-nutrition-foods 实拉生成。"
                        "items 只含「单品且有营养数据」的求解器输入；"
                        "combos/merch 仅供参考，不进求解器。"
                    ),
                    "_source": "麦当劳 MCP · query-meals / list-nutrition-foods",
                    "store_code": store_codes[0] if store_codes else "",
                    "coverage": {
                        "menu_total": len(menu_items),
                        "solver_items": len(singles),
                        "combos": len(combos),
                        "merch": len(merch),
                    },
                    "items": singles,
                    "combos": [{"name": c["name"], "price": c["price"],
                                "category": c["category"]} for c in combos],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"✅ 已写入 {mpath}：求解器可用单品 {len(singles)} 项"
            f"（菜单共 {len(menu_items)} 项，其中套餐 {len(combos)}、周边 {len(merch)}）"
        )
        if len(singles) < 15:
            warn.append(
                f"menu coverage low: only {len(singles)} single items with nutrition "
                f"out of {len(menu_items)} menu entries"
            )

    if warn:
        print(f"\n⚠️  {len(warn)} 条降级警告，已记录在 meta.notes")
    return 0


def _dig(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _parse_account(payload: Any) -> dict[str, Any]:
    d = (payload or {}).get("data") or {}
    return {
        "points_available": _to_float(d.get("availablePoint")),
        "points_total": _to_float(d.get("accumulativePoint")),
        "points_expiring": _to_float(d.get("currentMouthExpirePoint")),
        "points_expired": _to_float(d.get("expiredPoint")),
    }


def _parse_coupons(payload: Any) -> list[dict[str, Any]]:
    d = (payload or {}).get("data")
    items = d if isinstance(d, list) else ((d or {}).get("list") if isinstance(d, dict) else []) or []
    out = []
    for c in items:
        if not isinstance(c, dict):
            continue
        out.append({
            "name": str(c.get("couponName") or c.get("name") or ""),
            "status": str(c.get("status") or c.get("couponStatus") or "available"),
            "value": _to_float(c.get("couponAmount") or c.get("value") or c.get("discountAmount")),
        })
    return out


def _parse_mall(payload: Any) -> list[dict[str, Any]]:
    d = (payload or {}).get("data")
    if isinstance(d, list):
        items = d[0].get("list") if d and isinstance(d[0], dict) else []
    elif isinstance(d, dict):
        items = d.get("list") or []
    else:
        items = []
    out = []
    for m in items or []:
        if not isinstance(m, dict):
            continue
        out.append({
            "product_name": str(m.get("productName") or m.get("name") or ""),
            "points_cost": _to_float(m.get("point") or m.get("pointsCost")),
            "created_at": str(m.get("createTime") or m.get("createdAt") or ""),
        })
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="collect", description="从麦当劳 MCP 采集数据并落盘")
    p.add_argument("--out", "-o", default=".mcd-diff/raw.json", help="raw.json 输出路径")
    p.add_argument("--menu", "-m", default=".mcd-diff/menu.json", help="menu.json 输出路径")
    p.add_argument("--url", default=DEFAULT_URL, help=f"MCP 服务地址（默认 {DEFAULT_URL}）")
    p.add_argument("--token", default=None, help="MCP Token（默认读环境变量或 WorkBuddy 配置）")
    p.add_argument("--timeout", type=int, default=60, help="单次请求超时秒数")
    p.add_argument("--window-days", type=int, default=365, help="统计窗口天数（写入 meta）")
    p.add_argument("--max-stores", type=int, default=3, help="最多尝试几个门店拉菜单")
    p.add_argument(
        "--menu-times",
        default="08:00,12:00,15:30",
        help="额外探测的时段（HH:MM，逗号分隔）。菜单随时段变化，"
             "多拉几次才能拿到早餐等分时段品项；传空字符串可关闭。",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return collect(args)
    except McpError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
