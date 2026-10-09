#!/usr/bin/env python3
"""把示例报告打包成 GitHub Pages 的在线 Demo。

做的事只有两件：

1. 把 ``examples/sample_diff.html`` 原样搬到 ``docs/index.html``，
   只在 ``<body>`` 之后注入一条顶部横幅 —— 因为报告本身是给「用户自己」看的，
   页脚那句声明对真实用户足够了；但挂在公网首页时，访客第一眼看到的是
   「¥2660 → ¥2289」这种数字，很容易被误读成真实账单。所以在线版需要一条
   第一眼就能看到的说明。
2. 生成 ``docs/.nojekyll``（关闭 Jekyll），避免 Pages 对 ``docs/`` 下的
   Markdown 做多余处理。

用法::

    python scripts/build_demo.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "examples" / "sample_diff.html"
DST = ROOT / "docs" / "index.html"
NOJEKYLL = ROOT / "docs" / ".nojekyll"

BANNER = (
    '<div style="background:#FFC72C;color:#1a1a1a;padding:11px 18px;text-align:center;'
    "font:500 14px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
    'border-bottom:1px solid rgba(0,0,0,.12)">'
    "<b>示例报告</b>　·　订单数据为程序生成的<b>虚构数据</b>（74 单），"
    "菜单价格与营养来自麦当劳 MCP 的<b>真实数据</b>　·　"
    '<a href="https://github.com/weixiao121/mcd-diff" style="color:#1a1a1a;text-decoration:underline">'
    "查看项目源码</a>"
    "</div>"
)

BODY_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)


def main() -> int:
    if not SRC.exists():
        print(
            f"找不到 {SRC}\n请先运行：\n"
            "  python examples/generate_sample_data.py\n"
            "  python scripts/analyze.py diff --input examples/sample_raw.json "
            "--out examples/sample_diff.html --mode thrift",
            file=sys.stderr,
        )
        return 1

    html = SRC.read_text(encoding="utf-8")
    m = BODY_RE.search(html)
    if m is None:
        print("示例报告里找不到 <body> 标签，无法注入横幅", file=sys.stderr)
        return 1

    out = html[: m.end()] + "\n" + BANNER + html[m.end() :]
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(out, encoding="utf-8")
    NOJEKYLL.write_text("", encoding="utf-8")

    print(f"已生成 {DST.relative_to(ROOT)}（{len(out)} 字符）")
    print(f"已生成 {NOJEKYLL.relative_to(ROOT)}")
    print("下一步：在仓库 Settings → Pages 里把 Source 设为 Deploy from a branch / main / docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
