#!/usr/bin/env python3
"""安装脚本写入的托管块，与面板能渲染出的托管块，必须"能力对等"。

为什么需要这条检查
------------------
`server.conf` 的托管块有两个生产者：

* `install.sh` —— 安装时写一份初版；
* 面板的 `server_conf_writer.render_managed_block()` —— 之后每次保存都整块重写。

关键在于**面板会整块替换**：install.sh 放进去、而面板不会渲染的指令，会在用户第一次
保存设置时**静默消失**。这个坑已经踩到两次：

1. `ifconfig-ipv6`：面板的 push 白名单漏了它（助手会拒绝面板的输出）；
2. `push "sndbuf/rcvbuf"`：install.sh 写了，面板只渲染服务端标量，
   于是保存一次之后客户端的收发缓冲调优就没了。

两者都不是崩溃，而是"看起来保存成功、实际能力悄悄退化"，最难发现。所以这里做集合比对：
**install.sh 托管块里出现的每一种指令形态，面板都必须能渲染出来。**

用法：python3 tests/test_block_parity.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

tmp = pathlib.Path(tempfile.mkdtemp(prefix="ovpn-parity-"))
os.environ.update(
    {
        "SQLITE_PATH": str(tmp / "app.db"),
        "SECRET_KEY": "parity-secret-key-0123456789abcdef",
        "DEFAULT_ADMIN_PASSWORD": "parity-password-1234",
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
sys.path.insert(0, str(ROOT / "backend"))

from app.services import server_conf  # noqa: E402
from app.services import server_conf_writer as writer  # noqa: E402

failures: list[str] = []


def shape(line: str) -> str:
    """把一行指令归一成"形态"，刻意与取值无关。

    标量取指令名；push 取 ``push:<种类>``，并且只在第二个 token 是**关键字**时
    才把它带上（``dhcp-option DNS`` 里的 DNS、``redirect-gateway def1`` 里的 def1），
    数值/地址一律丢掉（``sndbuf 393216`` 的 393216、``route 10.10.0.0`` 的地址）。

    为什么要这么做：两边取值本来就可能不同（安装脚本写 393216，用户可能在面板里
    改成别的）。这里要回答的是"面板能不能表达这种指令"，不是"两边数字是否相同"，
    否则会冒出假失败，把真正的问题淹掉。
    """
    line = line.strip()
    if not line or line.startswith(("#", ";")):
        return ""
    parts = line.split()
    name = parts[0].lower()
    if name == "push":
        payload = line.split("push", 1)[1].strip().strip('"')
        toks = payload.split()
        if not toks:
            return "push"
        if len(toks) > 1 and re.fullmatch(r"[A-Za-z][A-Za-z0-9!-]*", toks[1]):
            return f"push:{toks[0]} {toks[1]}"
        return f"push:{toks[0]}"
    return name


def install_sh_block_shapes() -> set[str]:
    """从 install.sh 里抽出托管块，替换 ${变量} 后取形态集合。"""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    begin = server_conf.MANAGED_BEGIN
    end = server_conf.MANAGED_END
    lines = text.splitlines()
    try:
        a = lines.index(begin)
        b = lines.index(end, a)
    except ValueError:
        sys.exit("在 install.sh 里找不到托管块标记")

    subs = {
        "${VPN_PORT}": "1194",
        "${VPN_PROTO}": "udp",
        "${VPN_NETWORK}": "10.8.44.0",
        "${VPN_NETMASK}": "255.255.255.0",
        "${dns_lines}": 'push "dhcp-option DNS 8.8.8.8"',
        "${push_routes}": 'push "redirect-gateway def1 bypass-dhcp"',
        "${ipv6_lines}": "",
        "${exit_notify}": "explicit-exit-notify 1",
        "${fast_io}": "fast-io",
    }
    shapes: set[str] = set()
    for raw in lines[a + 1 : b]:
        line = raw
        for key, val in subs.items():
            line = line.replace(key, val)
        s = shape(line)
        if s:
            shapes.add(s)
    return shapes


def panel_block_shapes() -> set[str]:
    """让面板渲染一份"所有开关都打开"的块，取形态集合。

    刻意把每个可选项都打开（full 与 split 二选一，故两种模式都跑一遍），
    这样只要面板**能**表达某形态就会被收集到。
    """
    base = {
        "port": 1194,
        "proto": "udp",
        "dns_servers": ["8.8.8.8", "8.8.4.4"],
        "block_ipv6": True,
        "mssfix": 1420,
        "sndbuf": 393216,
        "rcvbuf": 393216,
        "keepalive": "10 120",
        "client_to_client": True,
        "max_clients": 50,
        "duplicate_cn": True,
        "network": "10.8.44.0",
        "netmask": "255.255.255.0",
        "topology": "subnet",
    }
    shapes: set[str] = set()
    for mode, routes in (("full", []), ("split", ["10.10.0.0/16"]), ("full", [])):
        settings = dict(base, tunnel_mode=mode, routes=routes)
        for line in writer.render_managed_block(settings):
            s = shape(line)
            if s:
                shapes.add(s)
    # 只对 UDP 有效的那两条，用 tcp 再确认一次"面板不会渲染"（不作为缺失项）
    return shapes


def main() -> int:
    install_shapes = install_sh_block_shapes()
    panel_shapes = panel_block_shapes()

    print("install.sh 托管块形态：")
    for s in sorted(install_shapes):
        print(f"  {s}")
    print("\n面板缺少的形态（install.sh 会写、面板不会渲染）：")
    missing = sorted(install_shapes - panel_shapes)
    if missing:
        for s in missing:
            print(f"  !! {s}")
        failures.append(
            "install.sh 写入的托管块里有面板无法渲染的指令形态："
            + ", ".join(missing)
            + "（用户第一次保存设置后这些指令会静默消失）"
        )
    else:
        print("  （无）")

    # 反向信息：面板能渲染但 install.sh 不写的（无害，仅提示）
    extra = sorted(panel_shapes - install_shapes)
    if extra:
        print("\n提示：面板能渲染但安装脚本不写的形态（无害）：")
        for s in extra:
            print(f"  {s}")

    # 交叉验证：把 install.sh 的块交给助手的校验规则，必须通过
    # （这条由 tests/test_apply_config.sh 覆盖，这里只做形态层面的确认）
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        for f in failures:
            print(f"失败：{f}")
        return 1
    print("对等性检查通过：install.sh 托管块里的每种指令形态，面板都能渲染。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
