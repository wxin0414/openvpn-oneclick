"""版本号的唯一来源。

面板右下角显示的版本必须与 VERSION.txt 一致，否则用户看到的就是错的。此前前端直接
``import { version } from "../package.json"``，而发版流程只改 VERSION.txt 与 install.sh
里的 SCRIPT_VERSION，于是页脚永远停在旧版本（实测停在 1.4.0，实际已是 1.4.4）。

现在改成：pack.sh 把 VERSION.txt 打进制品根目录 -> 后端启动时读它 -> 前端向后端要。
package.json 的 version 保留作构建期兜底，由 tests/check_versions.py 保证几处一致。
"""

from __future__ import annotations

import os
from pathlib import Path


def _built() -> str:
    """打包时写入的常量（最可靠：不依赖运行时文件布局）。"""
    try:
        from app._build_version import VERSION  # type: ignore[attr-defined]

        return str(VERSION).strip()
    except Exception:  # noqa: BLE001 - 源码直接运行、没有构建产物时走下面的文件探测
        return ""


def _read() -> str:
    built = _built()
    if built and built != "unknown":
        return built
    override = os.environ.get("OVPN_VERSION_FILE")
    candidates = []
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve()
    # 制品布局：<root>/backend/app/version.py，<root>/VERSION.txt
    # 源码布局：<repo>/backend/app/version.py，<repo>/VERSION.txt
    for depth in (2, 3):
        try:
            candidates.append(here.parents[depth] / "VERSION.txt")
        except IndexError:
            continue
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"


APP_VERSION = _read()
