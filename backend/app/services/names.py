"""客户端名称（common_name）的唯一校验入口。

为什么必须有
------------
这个名字会**直接拼进文件路径**并作为 EasyRSA 的命令行参数：

    pki/issued/<cn>.crt、pki/private/<cn>.key、ccd/<cn>
    easyrsa build-client-full <cn>

只限长度是不够的。实测把 common_name 设成 ``../../escaped-by-cn``，``ccd.write_ccd``
会把文件写到 CCD 目录之外（面板用户可写范围内的任意文件）；``-foo`` 这类名字还可能
被 easyrsa 当成命令行选项（参数注入）。

规则（刻意宽到能用，只禁危险字符）
----------------------------------
允许：中文、字母、数字、下划线、点、连字符、空格等一切正常字符
禁止：``/`` 与 ``\\``（路径分隔符）、NUL 与控制字符、连续的 ``..``、
      以 ``-`` 开头（会被当成命令行选项）、以 ``.`` 开头、纯点号、长度 > 64

即"文件名安全 + 不能是选项"这两条底线，而不是把字符集收窄成 ASCII —— 收窄会让
中文客户端名直接不可用，属于过度限制。
"""

from __future__ import annotations

import re

# pydantic 的 Field(pattern=...) 用 Rust 正则，**不支持 (?!...) 前瞻**，
# 所以这里只表达"不含路径分隔符与 NUL"；其余规则由 is_safe_name 负责。
NAME_PATTERN = r"^[^/\\\x00-\x1f]{1,64}$"

_NAME_RE = re.compile(NAME_PATTERN)

NAME_HINT = "不能包含 / 或 \\\\、不能以 - 或 . 开头、不能包含连续的点（..）、长度 1-64"


def is_safe_name(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    if len(value) > 64:
        return False
    if "/" in value or "\\" in value or "\x00" in value:
        return False
    if any(ord(ch) < 0x20 for ch in value):
        return False
    if ".." in value:
        return False
    if value[0] in "-.":
        return False
    if not value.replace(".", ""):
        return False
    return True


def ensure_safe_name(value: str | None, field: str = "common_name") -> str:
    """校验失败时抛 ValueError（调用方决定转成 400 还是任务失败）。"""
    if not is_safe_name(value):
        raise ValueError(f"{field} 不合法：{NAME_HINT}")
    return value  # type: ignore[return-value]
