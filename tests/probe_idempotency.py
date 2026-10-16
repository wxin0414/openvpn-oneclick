#!/usr/bin/env python3
"""诊断 J29：build_candidate 到底是不是幂等的？如果不是，差异在哪里？

幂等性很重要：用户在面板里保存两次，配置不应该越改越乱（比如注释越攒越多、
标记块变成两个）。这里直接跑两遍并打印差异，比读代码更直接。

用法：backend/.venv/bin/python tests/probe_idempotency.py
"""

from __future__ import annotations

import difflib
import os
import pathlib
import sys
import tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="ovpn-idem-"))
os.environ.update(
    {
        "SQLITE_PATH": str(tmp / "app.db"),
        "SECRET_KEY": "probe-secret-key-0123456789abcdef",
        "DEFAULT_ADMIN_PASSWORD": "probe-password-1234",
        "DEPLOY_ENV": "PROD",
        "BACKGROUND_STATUS_SYNC": "false",
        "SERVER_CONF_PATH": str(tmp / "server.conf"),
        "EASYRSA_PATH": str(tmp / "easy-rsa"),
        "OPENVPN_BASE_PATH": str(tmp / "openvpn"),
        "OPENVPN_CLIENT_EXPORT_PATH": str(tmp / "client"),
        "CCD_PATH": str(tmp / "ccd"),
        "TA_KEY_PATH": str(tmp / "ta.key"),
    }
)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.services import server_conf_writer as w  # noqa: E402

# 与 backend/tests/test_fixes.py 的 J 节夹具逐行一致
ADOPTED = "\n".join(
    [
        "# 由 hwdsl2/openvpn-install 生成",
        "port 1194",
        "proto tcp",
        "dev tun",
        "user nobody",
        "topology subnet",
        "server 10.8.44.0 255.255.255.0",
        'push "dhcp-option DNS 1.1.1.1"',
        'push "route 10.20.0.0 255.255.0.0"',
        "fast-io",
        "explicit-exit-notify 1",
        "keepalive 10 60",
        "client-to-client",
        'push "block-outside-dns"',
        "log-append /var/log/openvpn/openvpn.log",
        "crl-verify /etc/openvpn/server/crl.pem",
        "management 127.0.0.1 7505",
        "ca /etc/openvpn/server/ca.crt",
        "tls-crypt /etc/openvpn/server/ta.key",
        "verb 3",
    ]
) + "\n"

SETTINGS = {
    "port": 443,
    "proto": "udp",
    "dns_servers": ["8.8.8.8"],
    "tunnel_mode": "split",
    "routes": ["10.10.0.0/16"],
    "block_ipv6": False,
    "mssfix": 1400,
    "sndbuf": None,
    "rcvbuf": None,
    "keepalive": "10 120",
    "client_to_client": True,
    "max_clients": 50,
    "duplicate_cn": False,
    "network": "10.8.44.0",
    "netmask": "255.255.255.0",
    "topology": "subnet",
}

out1 = w.build_candidate(ADOPTED, SETTINGS)
out2 = w.build_candidate(out1, SETTINGS)
out3 = w.build_candidate(out2, SETTINGS)

print(f"第 1 次 -> 第 2 次 幂等: {out1 == out2}")
print(f"第 2 次 -> 第 3 次 幂等: {out2 == out3}")

if out1 != out2:
    print("\n--- 第 1 次 与 第 2 次 的差异 ---")
    for line in difflib.unified_diff(
        out1.splitlines(), out2.splitlines(), "first", "second", lineterm="", n=1
    ):
        print(line)

print("\n--- 第 1 次产出的托管块 ---")
lines = out1.splitlines()
try:
    a = lines.index(w.MANAGED_BEGIN)
    b = lines.index(w.MANAGED_END)
    for line in lines[a : b + 1]:
        print(f"  {line}")
except ValueError:
    print("  未找到托管块标记")

print("\n--- 第 1 次产出里被注释掉的行 ---")
for line in lines:
    if line.lstrip().startswith("#") and any(
        kw in line for kw in ("port", "proto", "push", "server")
    ):
        print(f"  {line}")

import shutil  # noqa: E402

shutil.rmtree(tmp, ignore_errors=True)
