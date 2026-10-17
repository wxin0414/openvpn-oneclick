#!/usr/bin/env python3
"""校验前后端的数值区间一致（纯静态，不需要 Linux / 数据库 / 服务）。

为什么需要它
------------
这些上限在两个地方各写一遍：
  * 后端 `app/services/server_conf.py::validate_editable` —— 真正的权威
  * 前端 `views/SettingsView.vue` —— 只是为了提前拦，减少无谓往返

两边漂移过一次：页面允许 mssfix 到 65535、缓冲区到 16 MiB，而后端只到 1500 与
10485760，于是用户能填进去、点保存必然 400，只看到「保存失败」。这个脚本把
"必须一致"变成可自动检查的事实，防止再次漂移。

用法：python tests/check_ranges.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend" / "app" / "services" / "server_conf.py"
FRONTEND = ROOT / "frontend" / "src" / "views" / "SettingsView.vue"
PANEL_API = ROOT / "backend" / "app" / "api" / "api_v1" / "endpoints" / "panel.py"

problems: list[str] = []
checked = 0


def ok(msg: str) -> None:
    global checked
    checked += 1
    print(f"  [PASS] {msg}")


def bad(msg: str) -> None:
    global checked
    checked += 1
    problems.append(msg)
    print(f"  [FAIL] {msg}")


def read(path: Path) -> str:
    if not path.is_file():
        bad(f"文件不存在：{path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")


print("== 后端 validate_editable 的上限 ==")
backend_src = read(BACKEND)
mssfix_backend = None
generic_backend = None
if backend_src:
    m = re.search(r'key == "mssfix"[^\n]*value\s*<=\s*(\d+)', backend_src)
    if m:
        mssfix_backend = int(m.group(1))
        ok(f"后端 mssfix 上限 = {mssfix_backend}")
    else:
        bad("没能在后端找到 mssfix 的上限判断（正则失配，可能被重写了）")
    m = re.search(r'key != "mssfix"[^\n]*value\s*<=\s*(\d+)', backend_src)
    if m:
        generic_backend = int(m.group(1))
        ok(f"后端通用数值上限 = {generic_backend}")
    else:
        bad("没能在后端找到通用数值上限判断（正则失配，可能被重写了）")

print("== 前端常量 ==")
frontend_src = read(FRONTEND)
frontend: dict[str, int] = {}
if frontend_src:
    for name in ("MSSFIX_MAX", "BUFFER_MAX", "MAX_CLIENTS_MAX"):
        m = re.search(rf"const\s+{name}\s*=\s*(\d+)\s*;", frontend_src)
        if m:
            frontend[name] = int(m.group(1))
            ok(f"前端 {name} = {frontend[name]}")
        else:
            bad(f"前端缺少常量 {name}（应集中定义，不要散落魔法数字）")

print("== 一致性 ==")
if mssfix_backend is not None and "MSSFIX_MAX" in frontend:
    if frontend["MSSFIX_MAX"] == mssfix_backend:
        ok(f"mssfix 前后端一致（{mssfix_backend}）")
    else:
        bad(f"mssfix 漂移：前端 {frontend['MSSFIX_MAX']} vs 后端 {mssfix_backend}")

if generic_backend is not None:
    for name in ("BUFFER_MAX", "MAX_CLIENTS_MAX"):
        if name in frontend:
            if frontend[name] == generic_backend:
                ok(f"{name} 与后端一致（{generic_backend}）")
            else:
                bad(f"{name} 漂移：前端 {frontend[name]} vs 后端 {generic_backend}")

print("== 前端不得再出现旧的魔法数字 ==")
if frontend_src:
    if "16777216" in frontend_src:
        bad("前端仍残留 16777216（旧的 16 MiB 缓冲区上限）")
    else:
        ok("前端已无 16777216（旧的 16 MiB 缓冲区上限）")
    # 端口字段本身确实是 1-65535，允许它出现；但其它字段不得再用裸 65535。
    offenders = [
        line.strip()
        for line in frontend_src.splitlines()
        if ':max="65535"' in line and "form.port" not in line
    ]
    if offenders:
        bad(f"前端仍有非端口字段用裸 65535：{offenders}")
    else:
        ok('前端 :max="65535" 只出现在端口字段上')
    # 端口本身确实是 1-65535，允许保留
    if re.search(r'form\.port,\s*1,\s*65535', frontend_src):
        ok("端口仍按 1-65535 校验（正确）")
    else:
        bad("端口的 1-65535 校验不见了，请确认")

print("== 面板设置接口的区间与助手一致 ==")
panel_src = read(PANEL_API)
if panel_src:
    if re.search(r"port:\s*int\s*\|\s*None\s*=\s*Field\([^)]*ge=1[^)]*le=65535", panel_src):
        ok("panel.port 声明为 1-65535")
    else:
        bad("panel.port 的 1-65535 声明没找到")
    if re.search(r"session_minutes:\s*int\s*=\s*Field\(ge=10,\s*le=10080\)", panel_src):
        ok("panel.session_minutes 声明为 10-10080")
    else:
        bad("panel.session_minutes 的 10-10080 声明没找到")
    if "MAX_CIDRS" in panel_src and re.search(r"MAX_CIDRS\s*=\s*32", panel_src):
        ok("放行来源上限 32 条")
    else:
        bad("放行来源的 32 条上限没找到")

print()
if problems:
    print(f"==== 结果：通过 {checked - len(problems)}，失败 {len(problems)} ====")
    for item in problems:
        print(f"  - {item}")
    sys.exit(1)
print(f"==== 结果：通过 {checked}，失败 0 ====")
print("前后端数值区间一致。")
