#!/usr/bin/env python3
"""保证版本号在几处保持一致，防止"页脚显示旧版本"这类漂移。

历史上踩过：发版只改 VERSION.txt 与 install.sh 的 SCRIPT_VERSION，而前端页脚读的是
package.json 的 version，于是页脚永远停在旧值。这条断言把三处绑在一起。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    version_file = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()

    script = (ROOT / "install.sh").read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^SCRIPT_VERSION="([^"]+)"', script, re.MULTILINE)
    script_version = match.group(1) if match else "<missing>"

    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    pkg_version = pkg.get("version", "<missing>")

    problems = []
    if script_version != version_file:
        problems.append(f'install.sh 的 SCRIPT_VERSION={script_version} 与 VERSION.txt={version_file} 不一致')
    if pkg_version != version_file:
        problems.append(f'frontend/package.json version={pkg_version} 与 VERSION.txt={version_file} 不一致')

    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1
    print(f"  [PASS] 版本一致：{version_file}（VERSION.txt / SCRIPT_VERSION / package.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
