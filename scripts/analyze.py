#!/usr/bin/env python3
"""便捷入口：直接从仓库根目录运行，无需安装包。

用法：
    python scripts/analyze.py diff --input examples/sample_raw.json --out examples/sample_diff.html
    python scripts/analyze.py solve --budget 35 --kcal 800
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把 src/ 加入模块搜索路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_diff.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
