#!/usr/bin/env python3
"""检查 install.sh 的每个 render 调用是否覆盖了对应模板里的全部占位符。

为什么需要：模板里的 ``__XXX__`` 只要有一个没在 render 调用里给出，装完就是一份
坏配置——例如 systemd 单元里留下 ``ReadWritePaths=__CCD_PATH__``，或者 nginx 里
留下 ``allow __VPN_NETWORK__/__VPN_CIDR__;``。这类问题只有在真机上服务起不来时
才会暴露，所以在这里静态拦下来。

做法：从 install.sh 中解析出每个 ``render "${TPL_DIR}/<模板>"`` 调用块，
收集它传入的 KEY 集合，再和模板文件里的占位符比对。这样后续改动会自动同步，
不需要在测试里重复维护一份键值列表。

用法：python3 tests/check_templates.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
TEMPLATES = ROOT / "templates"

# render "${TPL_DIR}/x" "目标" \ 然后是一行或多行 "KEY VALUE [KEY VALUE ...]"
# 注意开头的 (?<![A-Za-z0-9_])：仓库里还有 panel_nginx_render "${TPL_DIR}/nginx.conf"，
# 它是以 render 结尾的**另一个函数**，参数形态也不同（不是 KEY VALUE 对）。
# 不加这个前置断言，正则会把 panel_nginx_render 误当成 render，报"占位符没提供"。
RENDER_RE = re.compile(r'(?<![A-Za-z0-9_])render\s+"\$\{TPL_DIR\}/([^"]+)"((?:[^\n]*\\\n)*[^\n]*)')
# 注意：同一个 render 调用的一行里可能有多个 KEY VALUE 对
# （例如 PANEL_HOME "$PANEL_HOME" PANEL_USER "$PANEL_USER"），
# 因此不能只在行首匹配，必须全局匹配 "大写键 + 值" 这种成对结构。
KEY_RE = re.compile(
    r"([A-Z][A-Z0-9_]*)\s+"
    r'(?:"[^"]*"'          # "字面量"
    r"|\$\{[^}]*\}"        # ${VAR}
    r"|\$[A-Za-z_][A-Za-z0-9_]*"  # $VAR
    r"|\S+)"               # 其它裸值
)
PLACEHOLDER_RE = re.compile(r"__([A-Z][A-Z0-9_]*)__")

# nginx 站点模板不走 install.sh 的 render()（占位符替换与两段托管块重写都在
# templates/panel-nginx.sh 里，root 助手也调它）。为了不丢"占位符漏填"的检查，
# 这里显式声明：模板 -> 由哪个库负责替换，然后校验每个占位符都在库里出现过。
LIB_RENDERERS = {
    "nginx.conf": "panel-nginx.sh",
}


def main() -> int:
    if not INSTALL.is_file():
        print(f"找不到 {INSTALL}")
        return 1
    source = INSTALL.read_text(encoding="utf-8")

    calls: dict[str, set[str]] = {}
    for match in RENDER_RE.finditer(source):
        template, block = match.group(1), match.group(2)
        calls.setdefault(template, set()).update(KEY_RE.findall(block))

    if not calls:
        print("没有从 install.sh 解析到任何 render 调用（正则可能失效了）")
        return 1

    problems: list[str] = []
    print(f"{'模板':<26} {'占位符':>6} {'已提供':>6}  结论")
    print("-" * 62)

    for template in sorted(calls):
        path = TEMPLATES / template
        if not path.is_file():
            problems.append(f"install.sh 引用了模板 {template}，但 templates/ 下不存在")
            print(f"{template:<26} {'-':>6} {'-':>6}  缺失文件")
            continue

        placeholders = set(PLACEHOLDER_RE.findall(path.read_text(encoding="utf-8")))
        provided = calls[template]
        missing = placeholders - provided

        status = "OK" if not missing else "FAIL"
        print(f"{template:<26} {len(placeholders):>6} {len(provided):>6}  {status}")
        if missing:
            problems.append(f"{template} 缺少这些占位符的取值：{', '.join(sorted(missing))}")

    # 反向检查：模板目录里有没有从未被 install.sh 用到的模板（死文件或漏装）。
    # 注意 systemctl-wrapper.sh 是用 install(1) 直接安装的，不需要 render，
    # 因此这里只检查文件名是否在 install.sh 中出现过。
    unused = []
    for path in sorted(TEMPLATES.iterdir()):
        if not path.is_file():
            continue
        if path.name in calls:
            continue
        if path.name not in source:
            unused.append(path.name)
    if unused:
        print(f"\n提示：以下模板既未被 render 使用，也未在 install.sh 中出现：{', '.join(unused)}")

    # 由库渲染的模板：占位符必须全部在库里被替换掉（否则装完就是坏配置）
    for template, lib_name in sorted(LIB_RENDERERS.items()):
        path = TEMPLATES / template
        lib = TEMPLATES / lib_name
        if not path.is_file():
            problems.append(f"LIB_RENDERERS 声明了 {template}，但 templates/ 下不存在")
            continue
        if not lib.is_file():
            problems.append(f"{template} 由 {lib_name} 渲染，但该库不存在")
            continue
        placeholders = set(PLACEHOLDER_RE.findall(path.read_text(encoding="utf-8")))
        lib_text = lib.read_text(encoding="utf-8")
        missing = sorted(p for p in placeholders if f"__{p}__" not in lib_text)
        status = "OK" if not missing else "FAIL"
        print(f"{template + ' -> ' + lib_name:<26} {len(placeholders):>6} {len(placeholders) - len(missing):>6}  {status}")
        if missing:
            problems.append(f"{template} 的占位符没有在 {lib_name} 里替换：{', '.join(missing)}")
        elif f'panel_nginx_render "${{' not in source and f'{lib_name}' not in source:
            problems.append(f"{lib_name} 未被 install.sh 使用")

    print("-" * 62)
    if problems:
        print(f"\n发现 {len(problems)} 个问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n全部模板的占位符都能被 install.sh 完整渲染。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
