#!/usr/bin/env python3
"""最小冒烟：确认整个 FastAPI 应用能导入、且新设置路由已注册。

为什么单独做这件事：settings.py / server_conf_writer.py 是新增模块，一旦有语法错误或
导入环，整个面板都起不来——而这在单元测试之外才会暴露。这里只做导入与路由表检查，
不碰任何真实配置。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="ovpn-smoke-"))
os.environ.update(
    {
        "SQLITE_PATH": str(tmp / "app.db"),
        "SECRET_KEY": "smoke-test-secret-key-0123456789abcdef",
        "DEFAULT_ADMIN_PASSWORD": "smoke-test-password-1234",
        "DEPLOY_ENV": "PROD",
        "BACKGROUND_STATUS_SYNC": "false",
        "LOG_FILE": str(tmp / "panel.log"),
        "OPENVPN_STATUS_PATH": str(tmp / "openvpn-status.log"),
        "SERVER_CONF_PATH": str(tmp / "server.conf"),
        "EASYRSA_PATH": str(tmp / "easy-rsa"),
        "OPENVPN_BASE_PATH": str(tmp / "openvpn"),
        "OPENVPN_CLIENT_EXPORT_PATH": str(tmp / "client"),
        "CCD_PATH": str(tmp / "ccd"),
        "TA_KEY_PATH": str(tmp / "ta.key"),
        "OPENVPN_CRL_PATH": str(tmp / "crl.pem"),
    }
)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

failures: list[str] = []

try:
    from app.main import app
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] 应用无法导入：{type(exc).__name__}: {exc}")
    raise SystemExit(1)
print("[ok] app.main 导入成功")

# 路由表里必须能找到设置相关的三条
from app.core.route_audit import enumerate_routes  # noqa: E402

routes = enumerate_routes(app)
wanted = [
    ("/api/settings/server", "GET"),
    ("/api/settings/server", "PATCH"),
    ("/api/settings/reexport-all", "POST"),
]
for path, method in wanted:
    hit = any(p == path and method in methods for p, methods in routes)
    print(f"  {'[ok]' if hit else '[FAIL]'} {method:5} {path}")
    if not hit:
        failures.append(f"{method} {path} 未注册")

# 顺便跑一遍路由遮蔽检查（之前修过的 /import 那类问题）
from app.core.route_audit import shadowed_routes  # noqa: E402

shadowed = shadowed_routes(app)
print(f"  {'[ok]' if not shadowed else '[FAIL]'} 无路由被遮蔽（{len(routes)} 条路由）")
if shadowed:
    failures.append(f"存在被遮蔽的路由：{shadowed}")

# 新增模块可以直接导入
try:
    from app.services import server_conf_writer  # noqa: E402

    names = [n for n in ("render_managed_block", "build_candidate", "validate_candidate") if not hasattr(server_conf_writer, n)]
    print(f"  {'[ok]' if not names else '[FAIL]'} server_conf_writer 关键函数齐备")
    if names:
        failures.append(f"server_conf_writer 缺少：{names}")
except Exception as exc:  # noqa: BLE001
    print(f"  [FAIL] server_conf_writer 导入失败：{exc}")
    failures.append("server_conf_writer 导入失败")

import shutil  # noqa: E402

shutil.rmtree(tmp, ignore_errors=True)

if failures:
    print("\n冒烟失败：")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("\n冒烟通过。")
