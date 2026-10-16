"""server.conf 的统一解析（按修改时间缓存）。

修复背景
--------
原代码里有 **三份** 各自为政的 ``server.conf`` 解析：

* ``services/certs.py::_read_server_directives`` —— 生成客户端 .ovpn 时读 proto/cipher
* ``endpoints/clients.py::_load_server_network`` —— 校验固定 IP 时读网段
* ``endpoints/servers.py::_probe_vpn_ip`` —— 展示网关地址时读网段

三份实现细节不一致，而且 **``services/importer.py`` 干脆读错了文件**：
它用的是 ``settings.openvpn_base_path / "server.conf"``，而不是可配置的
``settings.server_conf_path``，因此在 ``server.conf`` 放在 ``server/`` 子目录的
部署里会静默退回默认值（port 1194 / proto tcp）。

这里收敛成一处：解析一次、按 mtime 缓存，谁需要谁调 ``load()``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# OpenVPN 未声明 topology 时的历史默认值
DEFAULT_TOPOLOGY = "net30"
DEFAULT_NETMASK = "255.255.255.0"


@dataclass(frozen=True)
class ServerConf:
    """server.conf 中与面板相关的配置项。"""

    path: str
    exists: bool = False
    port: int = 1194
    proto: str = "udp"
    network: IPv4Network | IPv6Network | None = None
    netmask: str | None = None
    topology: str = "unknown"
    cipher: str | None = None
    data_ciphers: str | None = None
    auth: str | None = None
    tls_mode: str | None = None
    status_version: int | None = None
    management: str | None = None
    crl_verify: str | None = None
    client_config_dir: str | None = None
    push_directives: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gateway(self) -> str | None:
        """网段的第一个可用地址（OpenVPN 服务端自身占用的地址）。"""
        if self.network is None:
            return None
        host = next(iter(self.network.hosts()), None)
        return str(host) if host else None

    def summary(self) -> dict:
        """供 /health/diagnostics 使用的精简视图。"""
        return {
            "path": self.path,
            "exists": self.exists,
            "port": self.port,
            "proto": self.proto,
            "topology": self.topology,
            "network": str(self.network) if self.network else None,
            "tls_mode": self.tls_mode,
            "has_management": bool(self.management),
            "status_version": self.status_version,
            "has_crl_verify": bool(self.crl_verify),
            "has_client_config_dir": bool(self.client_config_dir),
        }


_cache: dict[str, tuple[float, ServerConf]] = {}


def _strip_comments(lines: list[str]) -> list[list[str]]:
    """去掉注释与空行，返回按空白分列的 token 列表。"""
    parsed: list[list[str]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        # 行内注释：只在 # 前有空白时才算注释，避免误伤文件名里的 #
        for marker in (" #", "\t#", " ;", "\t;"):
            if marker in line:
                line = line.split(marker, 1)[0].rstrip()
        if line:
            parsed.append(line.split())
    return parsed


def _parse(path: Path) -> ServerConf:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("读取 server.conf 失败（%s）：%s", path, exc)
        return ServerConf(path=str(path), exists=False)

    tokens = _strip_comments(text.splitlines())

    port = 1194
    proto = "udp"
    network = None
    netmask: str | None = None
    topology = "unknown"
    cipher = data_ciphers = auth = tls_mode = None
    status_version: int | None = None
    management = crl_verify = client_config_dir = None
    pushes: list[str] = []

    for parts in tokens:
        key = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if key == "port" and arg and arg.isdigit():
            port = int(arg)
        elif key == "proto" and arg:
            proto = arg.lower()
        elif key == "topology" and arg:
            topology = arg.lower()
        elif key == "cipher" and arg:
            cipher = arg
        elif key == "data-ciphers" and arg:
            data_ciphers = arg
        elif key == "auth" and arg:
            auth = arg
        elif key in ("tls-crypt", "tls-auth"):
            tls_mode = key
        elif key == "status-version" and arg and arg.isdigit():
            status_version = int(arg)
        elif key == "management" and arg:
            management = f"{arg} {parts[2]}" if len(parts) > 2 else arg
        elif key == "crl-verify" and arg:
            crl_verify = arg
        elif key == "client-config-dir" and arg:
            client_config_dir = arg
        elif key == "server" and len(parts) >= 3:
            netmask = parts[2]
            try:
                network = ip_network(f"{parts[1]}/{parts[2]}", strict=False)
            except ValueError:
                logger.warning("无法解析 server 网段：%s %s", parts[1], parts[2])
                network = None
        elif key == "push" and len(parts) > 1:
            pushes.append(" ".join(parts[1:]).strip('"'))

    if topology == "unknown":
        # 不臆测：未声明时保持"未知"，由 CCD 生成侧按保守策略处理并提示运维显式声明。
        # （本项目所有真实配置都显式写了 topology subnet。）
        logger.debug("server.conf 未声明 topology，按未知处理：%s", path)

    return ServerConf(
        path=str(path),
        exists=True,
        port=port,
        proto=proto,
        network=network,
        netmask=netmask,
        topology=topology,
        cipher=cipher,
        data_ciphers=data_ciphers,
        auth=auth,
        tls_mode=tls_mode,
        status_version=status_version,
        management=management,
        crl_verify=crl_verify,
        client_config_dir=client_config_dir,
        push_directives=tuple(pushes),
    )


def load(*, force: bool = False) -> ServerConf:
    """解析 server.conf；文件未变化时复用上次结果。"""
    path = Path(settings.server_conf_path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ServerConf(path=str(path), exists=False)

    cached = _cache.get(str(path))
    if cached and cached[0] == mtime and not force:
        return cached[1]

    parsed = _parse(path)
    _cache[str(path)] = (mtime, parsed)
    return parsed


def invalidate() -> None:
    _cache.clear()


__all__ = ["ServerConf", "load", "invalidate", "DEFAULT_TOPOLOGY", "DEFAULT_NETMASK"]
