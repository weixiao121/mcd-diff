# 报名指引（M-CODE 麦当劳程序员创意开发大赛）

## 一、提交前自检清单

- [ ] 仓库已创建为 **Public**
- [ ] 根目录包含 **6 项必需内容**：
  - [ ] `README.md`
  - [ ] `CONTEST_DECLARATION.md`（**官方原文，一字未改**）
  - [ ] `MCP_INTEGRATION.md`
  - [ ] `mcp-config.example.json`（**只有 `${MCD_MCP_TOKEN}` 占位符**）
  - [ ] `workbuddy.md`（参加 WorkBuddy 专项奖励必需）
  - [ ] 源代码（`src/`、`scripts/`、`examples/`）
- [ ] 仓库中**没有** `.env`、没有真实 Token、没有密钥
- [ ] `git log` 历史里没有泄露过密钥
- [ ] `.mcd-diff/` 与生成的报告未被提交（已在 `.gitignore` 中）
- [ ] README 已写明「非麦当劳官方产品」
- [ ] 22 项单元测试全过：`PYTHONPATH=src python -m unittest discover -s tests`
- [ ] 示例产物已重新生成（`examples/sample_diff.{html,md,json}`、`sample_solve.md`）
- [ ] 首屏截图 `docs/images/hero.png` 与示例数据数字一致
- [ ] **同一账号下只保留本项目**（规则：同账号多个项目仅 Star 最高者进榜）

## 二、推送（仓库已初始化，只剩这一步）

本地 Git 仓库已经就绪，无需再 `git init`：

- 分支：`main`（5 个提交）
- 远端：`origin` → `https://github.com/weixiao121/mcd-diff.git`（已配置）
- 提交身份：`weixiao121 <weixiao121@users.noreply.github.com>`
- 待推送内容：37 个跟踪文件；`.mcd-diff/` 与生成的报告已被 `.gitignore` 排除

**先在 GitHub 网页端建一个空的 Public 仓库 `mcd-diff`（不要勾选 README / .gitignore / License），然后：**

```bash
git push -u origin main
```

> 推送到 GitHub 后，务必在仓库 Settings 中确认可见性为 **Public**。

<details>
<summary>若需要从零重建（一般用不到）</summary>

```bash
git init && git add . && git commit -m "feat: 麦门 Diff v1.0 —— 把你的麦当劳账单跑一遍 diff"
git branch -M main
git remote add origin https://github.com/weixiao121/mcd-diff.git
git push -u origin main
```

</details>

## 三、报名 Issue

前往活动主仓库发起 Issue：

- 仓库：<https://github.com/M-China/mcd-developer-innovation-challenge/issues>
- 标题：自定义（建议含项目名）

正文严格按以下格式（**≤1000 字，不要放图片**）：

```text
【参赛申请】
项目名称：麦门 Diff（mcd-diff）
项目地址：https://github.com/weixiao121/mcd-diff
项目简介：把你的麦当劳账单，跑一遍 diff——对过去一年发生的每一单，在「同等预算 ∩ 同等热量」约束下重新点一遍，算出本可以省下多少钱、少吃多少热量。全榜唯一对历史订单做「反事实推演」的项目：别人回答「我该买什么」，它回答「我本该买什么」。三条硬约束保证它是复盘而非说教——预算上限定为该单实付、热量上限定为该单实际摄入、并保留原单结构（品类 + 份数：不把饮料删掉、也不少给一份小食），因此差额 Δcost ≥ 0 是数学保证。输出含重开橱窗、差额账簿、三条重开路线（省钱向／扎实向／轻负担向）、时段与渠道归因、以及「现实宇宙 vs 平行宇宙」人格对照。工程上零第三方依赖、完全确定性、每一单都能复算：`scripts/collect.py` 自带标准库 MCP 客户端（手写 initialize → tools/call，兼容裸 JSON 与 SSE），可脱离任何宿主直接拉通真实采集；求解器对 41 项真实菜单做 112,791 种组合的精确全枚举，74 单全年重开耗时 4.9 秒（优化前 66 秒），结果逐位相同。
```

提交后等待系统在该 Issue 下回复「报名成功」。

## 四、拉 Star 传播路径（排名只看 Star）

- [ ] README 第一屏钩子已就位（真 diff 语法，GitHub 自动红绿渲染）
- [x] 报告首屏截图已就位（`docs/images/hero.png`，已按最终示例数据重截）
- [ ] 投稿：掘金、知乎、V2EX、少数派、即刻、小红书、技术社群
- [ ] 标题建议（三层钩子，覆盖不同人群）：
  - 数字钩子：《我把自己一年在麦当劳的 74 单全部重开了一遍，本可以省 ¥371》
  - 反直觉钩子（技术圈）：《这个项目不给你建议，它把你的历史订单全部重算了一遍》
  - 情绪钩子（社交平台）：《平行宇宙的我，一年少花 ¥371 —— 相当于 10.3 顿麦当劳。但我没有换，因为好吃》
- [ ] 每天查看 `RANKING.md` 追踪名次

> ⚠️ **严禁**：脚本刷 Star、多账号、购买 Star。真人自然收藏没问题，作弊会被取消资格。

## 五、时间节点

| 事项 | 时间（北京时间） |
|---|---|
| 报名及排名期 | 2026-10-09 10:30 — 2026-10-25 23:59 |
| 定榜数据采集 | 2026-10-26 00:00 |
| 奖品兑换 / 信息提交截止 | 2026-11-14 |
| 奖励发放 | 提交收货信息后约 2 周内 |
