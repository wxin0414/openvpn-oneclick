#!/usr/bin/env python3
"""跨组件契约检查：面板端（Python）与特权助手（Bash）必须对同一套约定达成一致。

为什么必须检查
--------------
"面板能改服务端配置"这件事靠一个约定维系：面板把想改的内容写进一个**带标记的托管块**，
root 的特权助手只认这个块、并且只允许改块里的指令。

两边分别是 Python 和 Bash 两套代码，常量是"抄"过去的。任何一边改了而另一边没改，
故障都不明显却很致命：

* 标记文本不一致 → 助手认不出面板写的块 → 把托管行当成"越权新增指令"，
  **面板每次保存都会被拒绝**；
* 托管指令集合不一致 → 面板写出助手不认的指令 → 同样每次保存都失败；
* push 前缀不一致 → 助手拒绝面板生成的 push → 保存失败。

所以这三组常量必须逐字比对。见 templates/apply-validate.sh 与
backend/app/services/server_conf.py、backend/app/services/server_conf_writer.py。

用法：python3 tests/check_apply_contract.py
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH_LIB = ROOT / "templates" / "apply-validate.sh"
CONF_PY = ROOT / "backend" / "app" / "services" / "server_conf.py"
WRITER_PY = ROOT / "backend" / "app" / "services" / "server_conf_writer.py"


def bash_assign(name: str, text: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"', text, re.MULTILINE)
    if not match:
        sys.exit(f"在 {BASH_LIB.name} 里找不到 {name}= 定义")
    return match.group(1)


def bash_marker(name: str, text: str) -> str:
    match = re.search(rf"^{name}='([^']*)'", text, re.MULTILINE)
    if not match:
        sys.exit(f"在 {BASH_LIB.name} 里找不到 {name}= 定义")
    return match.group(1)


def py_literal(path: pathlib.Path, name: str):
    """从 Python 源码里取出某个模块级赋值（不导入模块，避免依赖）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if name in targets:
            try:
                return ast.literal_eval(node.value)  # type: ignore[arg-type]
            except ValueError:
                # frozenset({...}) 之类的调用形式，退化成取里面的集合字面量
                for sub in ast.walk(node.value):
                    if isinstance(sub, (ast.Set, ast.Tuple, ast.List, ast.Dict)):
                        return ast.literal_eval(sub)
                sys.exit(f"{path.name}:{name} 无法静态求值")
    return None


def norm(seq) -> list[str]:
    return sorted(str(x).strip().lower() for x in seq)


def main() -> int:
    problems: list[str] = []
    bash = BASH_LIB.read_text(encoding="utf-8")

    # ---- 1) 托管块标记：必须逐字节一致 ----
    b_begin = bash_marker("MARK_BEGIN", bash)
    b_end = bash_marker("MARK_END", bash)
    p_begin = py_literal(CONF_PY, "MANAGED_BEGIN")
    p_end = py_literal(CONF_PY, "MANAGED_END")
    print("托管块标记")
    print(f"  助手 BEGIN : {b_begin!r}")
    print(f"  面板 BEGIN : {p_begin!r}")
    if b_begin != p_begin:
        problems.append("托管块起始标记不一致（面板写的块助手认不出来）")
    if b_end != p_end:
        problems.append("托管块结束标记不一致")
    print(f"  {'一致' if b_begin == p_begin and b_end == p_end else '不一致'}")

    # ---- 2) 托管指令集合 ----
    b_managed = norm(bash_assign("MANAGED_ALLOW", bash).split())
    p_managed = norm(py_literal(WRITER_PY, "MANAGED_DIRECTIVES") or [])
    p_scalars = norm(py_literal(CONF_PY, "MANAGED_SCALARS") or [])
    print("\n托管指令集合")
    print(f"  助手 MANAGED_ALLOW      : {len(b_managed)} 项")
    print(f"  面板 MANAGED_DIRECTIVES : {len(p_managed)} 项")
    only_bash = sorted(set(b_managed) - set(p_managed))
    only_py = sorted(set(p_managed) - set(b_managed))
    if only_bash:
        problems.append(f"助手允许但面板不会写（无害但说明已漂移）：{only_bash}")
    if only_py:
        problems.append(f"面板会写但助手不允许（会导致每次保存被拒）：{only_py}")
    if not only_bash and not only_py:
        print("  一致")
    # MANAGED_SCALARS 是子集（不含 push），必须是 MANAGED_ALLOW 的子集
    extra_scalars = sorted(set(p_scalars) - set(b_managed))
    if extra_scalars:
        problems.append(f"面板 MANAGED_SCALARS 里有助手不认的指令：{extra_scalars}")

    # ---- 3) push 前缀 ----
    b_push = norm(bash_assign("PUSH_ALLOW", bash).split())
    p_push = norm(py_literal(CONF_PY, "PUSH_ALLOW_PREFIXES") or [])
    print("\npush 允许前缀")
    print(f"  助手 PUSH_ALLOW          : {b_push}")
    print(f"  面板 PUSH_ALLOW_PREFIXES : {p_push}")
    if sorted(set(b_push)) != sorted(set(p_push)):
        problems.append(
            "push 前缀不一致（面板生成的 push 可能被助手拒绝，或助手的白名单里没有面板需要的形态）"
        )
    else:
        print("  一致")

    print()
    if problems:
        print(f"发现 {len(problems)} 个契约问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("面板与助手的契约完全一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
