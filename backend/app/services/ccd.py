"""CCD（client-config-dir）文件生成。

修复的问题
----------
原实现把 ``ifconfig-push`` 的第二个参数写死成 ``255.255.255.0``：

    lines.append(f"ifconfig-push {fixed_ip} 255.255.255.0")

在 ``topology subnet`` 下这个参数是**掩码**，因此只要服务端网段不是 /24
（例如 ``server 10.8.0.0 255.255.0.0``），下发的掩码就是错的，客户端隧道内
路由会异常——而代码注释自己也承认了这一点（"adjust if you maintain different
pool netmask"）。

更糟的是在 ``topology net30`` 下第二个参数是 /30 对端的**地址**而不是掩码，
写掩码会得到一条无意义的 peer 地址。这里按实际 topology 生成：

* ``subnet``：``ifconfig-push <ip> <掩码>``，掩码取自 server.conf
* ``net30``：``ifconfig-push <ip> <对端>``，net30 下客户端占用每个 /30 的较高地址，
  对端即 ``ip - 1``
* ``p2p``：只需要本地地址
* 未声明：沿用掩码形式（保守：net30 下多一个掩码只是路由不对，而 subnet 下
  把地址当掩码可能直接让该客户端配置报错），同时打日志提示显式声明 topology
"""

from __future__ import annotations

import logging
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Iterable

from app.core.config import get_settings
from app.models.client import Client
from app.services import server_conf

logger = logging.getLogger(__name__)
settings = get_settings()


def _format_route(route: str) -> str:
    """Convert route string to iroute line; accept '192.168.1.0/24' or '192.168.1.0 255.255.255.0'."""
    route = route.strip()
    if "/" in route:
        net = ip_network(route, strict=False)
        return f"iroute {net.network_address} {net.netmask}"
    parts = route.split()
    if len(parts) == 2:
        return f"iroute {parts[0]} {parts[1]}"
    # fallback: treat as comment
    return f"# invalid route format: {route}"


def _ifconfig_push(fixed_ip: str, conf: server_conf.ServerConf) -> str:
    """按服务端 topology 生成正确的 ifconfig-push 行。"""
    topology = (conf.topology or "unknown").lower()

    if topology == "p2p":
        return f"ifconfig-push {fixed_ip}"

    if topology == "net30":
        try:
            peer = ip_address(fixed_ip) - 1
        except ValueError:
            logger.warning("net30 下无法计算固定 IP 的对端地址：%s", fixed_ip)
            return f"ifconfig-push {fixed_ip} {conf.netmask or server_conf.DEFAULT_NETMASK}"
        return f"ifconfig-push {fixed_ip} {peer}"

    if topology == "unknown":
        logger.warning(
            "server.conf 未声明 topology，按 subnet 语义写入 ifconfig-push；"
            "建议显式写 'topology subnet' 以避免固定 IP 行为不确定"
        )

    # subnet 或未知：第二个参数是掩码，必须与服务端配置一致
    return f"ifconfig-push {fixed_ip} {conf.netmask or server_conf.DEFAULT_NETMASK}"


def write_ccd(client: Client, *, fixed_ip: str | None, routes: Iterable[str] | None = None) -> Path:
    """Write CCD file for client with optional static IP and per-client routes."""
    ccd_dir = Path(settings.ccd_path)
    created_dir = not ccd_dir.exists()
    ccd_dir.mkdir(parents=True, exist_ok=True)
    if created_dir:
        # OpenVPN 降权后要遍历这个目录才能读到 CCD 文件
        ccd_dir.chmod(0o755)

    conf = server_conf.load()
    lines: list[str] = []
    if fixed_ip:
        lines.append(_ifconfig_push(fixed_ip, conf))
    if routes:
        lines.extend(_format_route(r) for r in routes if r)

    path = ccd_dir / client.common_name
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    # OpenVPN 以 nobody 运行、面板以专用用户运行：CCD 文件必须对 OpenVPN 可读。
    # 显式给 0644，否则在 UMask=0077 这类加固配置下会变成 0600，
    # 表现为"面板里设了固定 IP / 路由，但客户端始终拿不到"且没有任何报错。
    path.chmod(0o644)
    return path
