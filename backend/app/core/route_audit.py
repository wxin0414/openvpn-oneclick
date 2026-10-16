"""路由遮蔽审计。

FastAPI / Starlette 按**声明顺序**匹配路由，且路径参数不参与优先级比较：
一旦 ``/{param}`` 声明在某个同方法的字面量路径之前，字面量路径就再也匹配不到，
请求会落到参数路由上（通常是 422 校验失败）。

上游代码里真实发生过这个问题：``openvpn.py`` 先声明 ``POST /{action}``
（``action`` 是 ``Literal["status","start","stop","restart"]``），后声明
``POST /import``；于是 ``/openvpn/import`` 永远返回
``422 Input should be 'status', 'start', 'stop' or 'restart'``，导入功能形同虚设。

实现选择
--------
路由清单取自 ``app.openapi()["paths"]``，原因有两个，都是踩过的坑：

1. FastAPI 较新版本把 ``include_router`` 的结果包成 ``_IncludedRouter``，
   ``app.routes`` 顶层只有一个条目，直接遍历什么都看不到（检查会"空转通过"）；
2. 手动递归展开那些包装层会**丢失 include 前缀**，"/api/openvpn/status" 会变成
   "/status"，于是和 "/{job_id}" 之类误判为互相遮蔽（假阳性）。

OpenAPI 的 paths 既带完整前缀，又保持注册顺序，正好满足需要。若取不到则返回空
列表，由调用方的"枚举不为空"断言把问题暴露出来，而不是静默通过。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\{[^}]+\}")


def enumerate_routes(app: Any) -> list[tuple[str, frozenset[str]]]:
    """返回 (完整路径, 方法集合) 列表，顺序即声明顺序。"""
    try:
        schema = app.openapi()
    except Exception:  # noqa: BLE001 - 取不到就交给调用方的护栏断言
        logger.warning("无法生成 OpenAPI schema，路由审计将为空", exc_info=True)
        return []

    paths = schema.get("paths") or {}
    result: list[tuple[str, frozenset[str]]] = []
    for path, operations in paths.items():
        methods = frozenset(
            key.upper() for key in operations if key.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
        )
        if methods:
            result.append((path, methods))
    return result


def shadowed_routes(app: Any) -> list[str]:
    """返回被更早声明的路径参数路由遮蔽的字面量路由描述列表。"""
    entries = enumerate_routes(app)
    problems: list[str] = []
    for index, (path_a, methods_a) in enumerate(entries):
        if not _PARAM_RE.search(path_a):
            continue
        pattern = re.compile("^" + _PARAM_RE.sub("[^/]+", path_a) + "$")
        for other, (path_b, methods_b) in enumerate(entries):
            if other <= index or _PARAM_RE.search(path_b):
                continue
            overlap = methods_a & methods_b
            if overlap and pattern.match(path_b):
                problems.append(f"{sorted(overlap)} {path_b} 被更早声明的 {path_a} 遮蔽")
    return problems


__all__ = ["enumerate_routes", "shadowed_routes"]
