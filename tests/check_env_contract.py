#!/usr/bin/env python3
"""校验打包进分发包的 env.template 与面板 Settings 的契约。

为什么必须有这个检查
--------------------
面板用 ``SettingsConfigDict(env_file=".env", extra="ignore")``：

* ``extra="ignore"`` 表示**认不出来的键会被静默丢弃**，绝不报错；
* 键名一旦写错（大小写、拼写），面板就会退回字段默认值。

而这个项目已经被同类问题坑过一次——``.env-example`` 里写的是 ``OVPNM_DB_PATH``，
但字段名是 ``sqlite_path``，于是该键被静默忽略，面板读的库和别处读的库可能不是同一个
（见 app/core/config.py 里的注释）。类似地，若模板漏了 ``EASYRSA_PATH``，面板就会去
``/etc/openvpn/easy-rsa`` 签发证书，而真实的 PKI 在 ``/etc/openvpn/server/easy-rsa``，
表现为"点签发没反应/报错"，很难排查。

所以这里做双向检查：
  1. 面板需要的每个设置，模板里都必须有对应键（有意的例外要写进 ALLOW_MISSING）；
  2. 模板里的每个键，都必须能对应到某个设置（否则就是拼错或被忽略的键）。

数据取自**分发包本身**（panel/ovpnmanager-panel.tar.gz），而不是源码目录，
这样检的就是真正会装到用户机器上的那份。

用法：python3 tests/check_env_contract.py
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARBALL = ROOT / "panel" / "ovpnmanager-panel.tar.gz"
TEMPLATE = ROOT / "templates" / "env.template"

CONFIG_MEMBER_SUFFIX = "backend/app/core/config.py"
SETTINGS_CLASS = "Settings"

# 有意不写进模板的设置：要么面板没有对应的运行期语义，要么由 systemd 单元直接给定。
# 任何新增的例外都必须显式加到这里，避免"漏了键"被无意放过。
ALLOW_MISSING = {
    "api_v1_prefix",  # 固定为 /api，不需要按装机定制
}

KEY_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def load_config_source() -> str:
    if not TARBALL.is_file():
        sys.exit(f"找不到分发包 {TARBALL}，请先运行 ./pack.sh")
    with tarfile.open(TARBALL, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith(CONFIG_MEMBER_SUFFIX):
                handle = tar.extractfile(member)
                if handle is not None:
                    return handle.read().decode("utf-8")
    sys.exit(f"分发包里找不到 {CONFIG_MEMBER_SUFFIX}")


def settings_env_keys(source: str) -> dict[str, set[str]]:
    """返回 {字段名: {它能识别的环境变量名}}。"""
    tree = ast.parse(source)
    klass = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == SETTINGS_CLASS
        ),
        None,
    )
    if klass is None:
        sys.exit(f"在 config.py 里找不到 {SETTINGS_CLASS} 类")

    result: dict[str, set[str]] = {}
    for stmt in klass.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        field = stmt.target.id
        keys = {field.upper()}
        if isinstance(stmt.value, ast.Call):
            for kw in stmt.value.keywords:
                if kw.arg == "validation_alias":
                    for node in ast.walk(kw.value):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            keys.add(node.value.upper())
                elif kw.arg == "alias" and isinstance(kw.value, ast.Constant):
                    keys.add(str(kw.value.value).upper())
        result[field] = keys
    return result


def template_keys(path: pathlib.Path) -> dict[str, str]:
    """返回模板里的 {键: 原始行}（跳过注释与空行）。"""
    keys: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_LINE_RE.match(line)
        if match:
            keys[match.group(1)] = line.rstrip()
    return keys


def main() -> int:
    config_source = load_config_source()
    fields = settings_env_keys(config_source)
    tpl = template_keys(TEMPLATE)
    tpl_upper = {k.upper(): k for k in tpl}

    # 方向 1：面板需要、模板缺失（会静默退回默认值）
    missing: list[tuple[str, str]] = []
    for field in sorted(fields):
        if field in ALLOW_MISSING:
            continue
        if not any(key in tpl_upper for key in fields[field]):
            default = "（无别名）" if len(fields[field]) == 1 else "/".join(sorted(fields[field]))
            missing.append((field, default))

    # 方向 2：模板里有、面板不认（拼错或被 extra="ignore" 丢弃）
    known = {key for keys in fields.values() for key in keys}
    unknown = sorted(k for k in tpl if k.upper() not in known and not k.startswith("__"))

    print(f"面板设置项 : {len(fields)}")
    print(f"模板键数   : {len(tpl)}")
    print()

    if missing:
        print(f"模板缺少 {len(missing)} 个面板设置（面板会静默使用默认值）：")
        for field, keys in missing:
            print(f"  - {field:<34} 需要的键：{keys}")
    if unknown:
        print(f"模板里有 {len(unknown)} 个面板不认识的键（会被静默忽略）：")
        for key in unknown:
            print(f"  - {key}")
            print(f"      {tpl[key]}")

    if not missing and not unknown:
        print("契约一致：面板需要的设置模板都有，模板里的键面板都认。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
