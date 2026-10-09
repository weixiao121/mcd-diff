# MCP 集成说明

> 本文说明「麦门 Diff」如何接入麦当劳 MCP Server、调用哪些 Tool、数据如何流转，
> 以及为什么整个流程**不可能产生任何订单**。

---

## 1. MCP Server 接入参数

| 项 | 值 |
|---|---|
| 服务地址 | `https://mcp.mcd.cn` |
| 传输协议 | **Streamable HTTP** |
| 鉴权方式 | `Authorization: Bearer <TOKEN>` |
| 限流 | 600 次 / 分钟 |
| Token 获取 | <https://open.mcd.cn/mcp> → 手机号登录 → 控制台 → 激活 |
| 配置示例 | 见仓库根目录 [`mcp-config.example.json`](mcp-config.example.json)（**仅占位符**） |

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

> ⚠️ **绝不把真实 Token 写进任何被提交的文件**。仓库内的配置示例只有 `${MCD_MCP_TOKEN}` 占位符，
> 本地运行时通过环境变量注入。

---

## 2. 用到的 Tool（只读）

| # | Tool | 名称 | 在本项目中的用途 | 输入 / 输出（关键字段） |
|---|---|---|---|---|
| 1 | `now-time-info` | 获取当前时间信息 | 确定报告截止时间、计算"距今多久" | → `datetime` |
| 2 | `order-list` | 查询历史订单 | **主数据源**：拉取近一年到店/外送订单 | → `orderId, orderTime, storeName, channel, totalAmount` |
| 3 | `query-order` | 查询订单详情 | 补齐单笔订单的商品明细与实付金额 | `orderId` → `items[], payAmount, discountAmount` |
| 4 | `query-meals` | 查询当前可售餐品 | **求解器输入**：在售单品、编码、价格、**品类** | → `meals[{name, code, price, category}]` |
| 5 | `query-meal-detail` | 查询餐品详情 | 解析套餐组成、识别可替换项，归一到单品 | `mealCode` → `comboItems[]` |
| 6 | `list-nutrition-foods` | 餐品营养信息列表 | **求解器输入**：能量 / 蛋白质 / 脂肪 / 碳水 / 钠 / 钙 | → `foods[{name, kcal, protein, fat, carbs, sodium, calcium}]` |
| 7 | `query-my-account` | 我的积分查询 | 平行宇宙画像维度之一（积分沉淀） | → `availablePoints, totalPoints, expiringPoints` |
| 8 | `query-my-coupons` | 我的优惠券查询 | 校验订单折扣率的合理性 | → `coupons[{name, status, expireAt}]` |
| 9 | `mall-order-list` | 麦麦商城订单查询 | 平行宇宙画像维度之一（积分去向） | → `mallOrders[{productName, pointsCost, createdAt}]` |
| 10 | `campaign-calendar` | 活动日历查询 | 为报告补充当月营销活动叙事 | → `campaigns[{name, startDate, endDate}]` |

### 2.1 明确**不调用**的 Tool

| Tool | 类别 | 为什么不用 |
|---|---|---|
| `create-order` | 交易 | 「重开」是**复盘**，不产生新订单 |
| `cancel-order` | 交易 | 同上 |
| `calculate-price` | 交易 | 定价计算由本地求解器完成，无需联网 |
| `draw-lottery` | 交易 | 涉及积分消耗 |
| `party-order-create` | 交易 | 涉及下单 |
| `mall-create-order` | 交易 | 涉及积分兑换 |
| `auto-bind-coupons` | 写入 | 会改变用户账户状态 |

> 代码层由 `src/mcd_diff/collectors.py` 的 `assert_readonly()` 显式拦截，
> 白名单以外的一律 `raise PermissionError`；`tests/test_smoke.py::TestSafetyBoundary` 持续验证。

---

## 3. 数据流与调用时序

### 3.1 总体时序

```text
用户: "帮我把这一年的麦当劳订单跑一遍 diff"
  │
  ├─(1) now-time-info ─────────────▶ 确定报告截止时间 T
  │
  ├─(2) order-list ────────────────▶ 近 12 个月订单列表（**重开的输入**）
  │
  ├─(3) query-order × N ───────────▶ 逐单补齐 items / payAmount / discount
  │        （对缺失 mealCode 的明细）
  │        └─ query-meal-detail ───▶ 套餐 → 单品拆分
  │
  ├─(4) query-meals ───────────────▶ 当前菜单（含 category，决定品类约束）
  ├─(5) list-nutrition-foods ──────▶ 单品营养表（一次拉取，本地做 join）
  │
  ├─(6) query-my-account ──────────▶ 积分账户
  ├─(7) query-my-coupons ──────────▶ 券持有（校验折扣率）
  ├─(8) mall-order-list ───────────▶ 积分兑换记录
  └─(9) campaign-calendar ─────────▶ 当月活动
        │
        ▼
   落盘 .mcd-diff/raw.json   ← 采集层终点（此处之后不再联网）
        │
        ▼
   本地计算（纯标准库、零网络）
     metrics → menu → solver → **regret** → persona
        │
        ▼
   📄 diff-report.html
```

### 3.2 采集与计算分离

| 层 | 模块 | 是否联网 | 职责 |
|---|---|---|---|
| 采集层 | WorkBuddy Agent + `collectors.py` | ✅ | 调用 MCP、归一化、落盘 `raw.json` |
| 计算层 | `metrics` / `menu` / `solver` / `regret` / `persona` | ❌ | 全部统计、求解、人格判定 |
| 渲染层 | `renderer.py` + `templates/diff.html` | ❌ | 生成自包含 HTML / MD / JSON |

**这样设计的好处**：计算层完全确定性、可单测（20 个用例）、可复现；
同一份 `raw.json` 永远得到同一份报告。

### 3.3 各 Tool 调用要点

- **`order-list` 分页**：按时间倒序翻页，直至超出 12 个月窗口或达到 `MAX_ORDERS`（默认 500）上限，防止无界请求。
- **`list-nutrition-foods` 只拉一次**：营养表是静态字典，本地用「单品名归一化」做 join，避免 N 次调用。
- **`query-meals` 必拉**：它提供 `category` 字段 —— **品类保留约束**依赖它。
- **`query-order` 按需调用**：仅对 `order-list` 中缺少明细的订单补调。
- **失败降级**：任一辅助工具（券/商城/活动）失败**不影响主流程**，对应板块降级为「数据不可用」，报告仍可生成。

---

## 4. 字段映射

### 4.1 订单

| MCP 字段 | 内部模型 | 说明 |
|---|---|---|
| `orderId` | `Order.order_id` | 订单号 |
| `orderTime` | `Order.time` | ISO 8601 → 用于时段归因 |
| `orderChannel` | `Order.channel` | dinein / pickup / delivery / mcdrive / party |
| `storeName` | `Order.store` | 门店名 |
| `payAmount` | `Order.total` | **实付金额 → 重开的预算上限** |
| `discountAmount` | `Order.discount` | 优惠金额 |
| `items[]` | `OrderItem[]` | 单品（含 qty / price / 营养） |

### 4.2 菜单

| MCP 字段 | 内部模型 | 说明 |
|---|---|---|
| `name` | `MenuItem.name` | 用于和订单单品做 `_norm_name` 归一化匹配 |
| `category` | `MenuItem.category` | **品类保留约束的关键** |
| `price` | `MenuItem.price` | 会被折扣系数缩放后送入求解器 |
| 营养表 join | `calories / protein / fat / carbs / sodium` | 来自 `list-nutrition-foods` |

### 4.3 折扣系数（口径对齐）

```python
gross  = Σ(该单每个单品的菜单原价 × qty)
factor = 该单实付金额 ÷ gross          # 例如 0.74 表示打了 7.4 折
重开时：priced_menu = [菜单价 × factor for 菜单]
```

这样「重开价」与「实付」处在同一口径 —— 等价于假设**那天的优惠力度不变，只是换个点法**。

---

## 5. 错误码与限流处理

| 场景 | 表现 | 处理 |
|---|---|---|
| Token 未配置 / 失效 | HTTP 401 | 提示前往 <https://open.mcd.cn/mcp> 重新激活并更新 `MCD_MCP_TOKEN` |
| 请求过于频繁 | HTTP 429 | 指数退避重试；本项目单次报告总调用量通常 < 20 次，远低于 600/分钟 |
| 门店 / 餐品不可用 | 业务错误码 | 跳过该项，降级处理，不中断报告 |
| 网络中断 | 超时 | 采集层终止，保留已落盘的 `raw.json`；计算层可离线继续 |

---

## 6. 合规与安全边界

- ✅ 仅调用**只读查询**类 Tool，不执行下单 / 取消 / 定价 / 抽奖 / 兑换。
- ✅ Token 仅从环境变量读取；仓库内 `mcp-config.example.json` **只有占位符**。
- ✅ 采集数据仅落盘本地 `.mcd-diff/`（已在 `.gitignore` 中），不联网、不上传。
- ✅ 报告输出为本地 HTML 文件，不包含真实 Token、账号凭证或他人个人信息。
- ✅ `examples/sample_raw.json` 由脚本随机生成，为**虚构数据**，不含任何真实用户信息。
- ✅ 所有「重开」结果限定在**该单自身条件内**，属机械换算与陈述，不构成消费/营养/健康建议。
- ✅ 不与其他品牌做任何对比，不贬低品牌 —— 报告的情绪落点是**「你没有重开，因为好吃」**。

---

## 7. 业务价值

| 角色 | 价值 |
|---|---|
| **麦门老客** | 拿到一份**可验证**的年度复盘：每一单的"本可以更好"都能复算、能反驳。 |
| **麦当劳** | 让用户重新审视自己的点单结构，**提升会员数据自我感知**；同时天然展示菜单的替代组合空间。 |
| **MCP 生态** | 提供一份「反事实分析」的 MCP Skill 范式：证明 MCP 不止能做交易和查询，还能对**已有数据**做严谨的重算。 |

**为什么这契合 MCP 的价值**：本项目几乎不碰交易类工具，而是把
`order-list` / `query-meals` / `list-nutrition-foods` 这些**沉淀型数据**
重新组织成对用户有意义的复盘 —— 这正是 MCP 作为「数据交互层」最擅长、
而传统点餐 App 最难提供的角度。
