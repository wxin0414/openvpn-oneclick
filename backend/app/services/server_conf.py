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
import re
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_network
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# OpenVPN 未声明 topology 时的历史默认值
DEFAULT_TOPOLOGY = "net30"
DEFAULT_NETMASK = "255.255.255.0"

# --------------------------------------------------------------------------- #
# 托管块（"所有设置都能在面板里改" 的落地形式）
# --------------------------------------------------------------------------- #
# server.conf 里用一个显式标记块承载"面板可编辑"的指令。面板**不直接改**
# server.conf（root 所有），而是把"想改成的样子"写成候选文件，再由 root 助手
# templates/apply-config.sh 校验后落地。标记文本必须与助手里的 MARK_BEGIN /
# MARK_END **完全一致**（助手用 `$0==b` 逐字符比较整行）。
MANAGED_BEGIN = "# >>> ovpn-oneclick managed (do not edit by hand) >>>"
MANAGED_END = "# <<< ovpn-oneclick managed <<<"

# 面板托管的标量指令（助手 MANAGED_ALLOW 的子集：这里不含 push）。
MANAGED_SCALARS: frozenset[str] = frozenset(
    {
        "port",
        "proto",
        "server",
        "topology",
        "mssfix",
        "sndbuf",
        "rcvbuf",
        "keepalive",
        "client-to-client",
        "max-clients",
        "duplicate-cn",
        "block-ipv6",
        "data-ciphers",
        "auth",
        "user",
        "group",
        "log-append",
        "status",
    }
)

# push 载荷允许出现的前缀（与助手 PUSH_ALLOW 一致）。
# ifconfig-ipv6 是 --block-ipv6 方案必需的：给客户端一个"看着合法"的 IPv6 配置，
# 配合 push "redirect-gateway ipv6" 才能把客户端的 IPv6 默认路由拉进隧道，
# 服务端的 block-ipv6 才有机会用 ICMPv6 no-route 回掉这些包。
PUSH_ALLOW_PREFIXES: tuple[str, ...] = (
    "dhcp-option",
    "redirect-gateway",
    "route",
    "sndbuf",
    "rcvbuf",
    "ifconfig-ipv6",
)

# block-ipv6 方案里下发给客户端的 IPv6 地址（与 install.sh 写入的一致）。
# 必须是 ULA（fd00::/8）之类不会被真实网络使用的地址：客户端拿到它之后
# 只会把 IPv6 默认路由指向隧道，不会真的去访问外部 IPv6。
BLOCK_IPV6_CLIENT_ADDRESS = "fd15:53b6:dead::2/64"
BLOCK_IPV6_CLIENT_REMOTE = "fd15:53b6:dead::1"

# 面板接管一行旧指令时加的注释前缀（含尾随空格，便于直接拼接）。
# 助手对托管块之外的注释行一律忽略（`/^[ \t]*#/ {next}`），因此这些注释既能让
# 新的托管值生效（OpenVPN 标量后者胜出、push 会累加），又不会让助手判成
# "候选引入了未授权指令"。已经带这个前缀的行会被原样保留（不重新生成），
# 这样"连续保存两次"不会改变文件内容——见 server_conf_writer.build_candidate。
RETIRED_PREFIX = "# [ovpn-oneclick retired] "

_VALID_TOPOLOGIES = ("subnet", "net30", "p2p")
# OpenVPN 2.4+ 未声明 topology 时按 subnet 处理。GET 接口的 topology 字段是
# 可编辑下拉框，不能塞 "unknown" 这种非法值，因此只在对外视图里按此兜底。
DEFAULT_EDITABLE_TOPOLOGY = "subnet"

# proto 只开放 udp/tcp 两种可编辑取值（见 validate_editable）；这里只校验
# keepalive 与三种 push 形态。
_KEEPALIVE_RE = re.compile(r"^\d+ \d+$")
# 系统用户/组名的字符规则（不查是否存在——那需要 shell，交给助手用真实结果回答）
_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")

# 可编辑的加密套件与认证摘要（OpenVPN 2.6 常用集合）。
# 刻意用白名单而不是自由字符串：写错一个套件名会让服务端直接起不来。
_VALID_CIPHERS: tuple[str, ...] = (
    "AES-256-GCM",
    "AES-128-GCM",
    "CHACHA20-POLY1305",
    "AES-256-CBC",
    "AES-128-CBC",
)
_VALID_AUTH: tuple[str, ...] = ("SHA512", "SHA256", "SHA1", "none")
_PUSH_DNS_RE = re.compile(r"^dhcp-option\s+DNS\s+(\S+)$", re.IGNORECASE)
_PUSH_ROUTE_RE = re.compile(r"^route\s+(\S+)(?:\s+(\S+))?$", re.IGNORECASE)
_PUSH_REDIRECT_RE = re.compile(r"^redirect-gateway\b", re.IGNORECASE)


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
    user: str | None = None
    group: str | None = None
    log_append: str | None = None
    status_file: str | None = None
    tls_mode: str | None = None
    status_version: int | None = None
    management: str | None = None
    crl_verify: str | None = None
    client_config_dir: str | None = None
    push_directives: tuple[str, ...] = field(default_factory=tuple)
    # --- 托管块的其余可编辑项（"所有设置都能在面板里改" 所需） ---
    mssfix: int | None = None
    sndbuf: int | None = None
    rcvbuf: int | None = None
    keepalive: str | None = None
    client_to_client: bool = False
    max_clients: int | None = None
    duplicate_cn: bool = False
    block_ipv6: bool = False

    @property
    def network_cidr(self) -> str | None:
        """``server`` 指令的 CIDR 形式（如 ``10.8.44.0/24``）。"""
        return str(self.network) if self.network is not None else None

    @property
    def netmask_or_default(self) -> str | None:
        """掩码；未声明 ``server`` 时返回 None（不臆测）。"""
        if self.netmask:
            return self.netmask
        if isinstance(self.network, (IPv4Network, IPv6Network)):
            return str(self.network.netmask)
        return None

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


def _is_retired(line: str) -> bool:
    """判断是否为面板自己写的"该指令已由托管块接管"注释。

    ``build_candidate`` 会把旧指令注释成 ``# [ovpn-oneclick retired] port 1194``；
    解析与候选重建都必须认出并丢弃它，否则每改一次配置就会多留一行垃圾注释。
    """
    return line.lstrip().startswith(RETIRED_PREFIX)


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
    user = group = None
    log_append = status_file = None
    status_version: int | None = None
    management = crl_verify = client_config_dir = None
    pushes: list[str] = []
    # 托管块的其余可编辑项
    mssfix: int | None = None
    sndbuf: int | None = None
    rcvbuf: int | None = None
    keepalive: str | None = None
    client_to_client = False
    max_clients: int | None = None
    duplicate_cn = False
    block_ipv6 = False

    for parts in tokens:
        key = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if key == "port" and arg and arg.isdigit():
            port = int(arg)
        elif key == "proto" and arg:
            proto = arg.lower()
        elif key == "topology" and arg:
            topology = arg.lower()
        elif key == "mssfix" and arg and arg.isdigit():
            mssfix = int(arg)
        elif key == "sndbuf" and arg and arg.isdigit():
            sndbuf = int(arg)
        elif key == "rcvbuf" and arg and arg.isdigit():
            rcvbuf = int(arg)
        elif key == "keepalive" and len(parts) >= 3:
            keepalive = f"{parts[1]} {parts[2]}"
        elif key == "client-to-client":
            client_to_client = True
        elif key == "max-clients" and arg and arg.isdigit():
            max_clients = int(arg)
        elif key == "duplicate-cn":
            duplicate_cn = True
        elif key == "block-ipv6":
            block_ipv6 = True
        elif key == "user" and arg:
            user = arg
        elif key == "group" and arg:
            group = arg
        elif key == "log-append" and arg:
            log_append = arg
        elif key == "status" and arg:
            status_file = arg
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
        user=user,
        group=group,
        log_append=log_append,
        status_file=status_file,
        auth=auth,
        tls_mode=tls_mode,
        status_version=status_version,
        management=management,
        crl_verify=crl_verify,
        client_config_dir=client_config_dir,
        push_directives=tuple(pushes),
        mssfix=mssfix,
        sndbuf=sndbuf,
        rcvbuf=rcvbuf,
        keepalive=keepalive,
        client_to_client=client_to_client,
        max_clients=max_clients,
        duplicate_cn=duplicate_cn,
        block_ipv6=block_ipv6,
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


# --------------------------------------------------------------------------- #
# 托管块：切分 / 读取 / 校验（面板的 GET 与写入侧共用）
# --------------------------------------------------------------------------- #
def split_managed(text: str) -> tuple[list[str], list[str], list[str]]:
    """把配置文本切成 ``(块前, 块内, 块后)`` 三段。

    找不到标记时返回 ``(全部行, [], [])``，调用方据此判断"托管块是否存在"。
    标记行本身不进任何一段（块内返回的是标记之间的纯指令行）。
    """
    lines = text.splitlines()
    before: list[str] = []
    managed: list[str] = []
    after: list[str] = []
    state = "before"
    for line in lines:
        if state == "before" and line.strip() == MANAGED_BEGIN:
            state = "inside"
            continue
        if state == "inside" and line.strip() == MANAGED_END:
            state = "after"
            continue
        if state == "before":
            before.append(line)
        elif state == "inside":
            managed.append(line)
        else:
            after.append(line)
    return before, managed, after


def managed_block_present(text: str) -> bool:
    """标记块是否成对存在（只有开始标记不算）。"""
    lines = [line.strip() for line in text.splitlines()]
    return MANAGED_BEGIN in lines and MANAGED_END in lines


def read_text() -> str | None:
    """读取当前 server.conf 原文；不存在或不可读时返回 None。"""
    try:
        return Path(settings.server_conf_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("读取 server.conf 失败（%s）：%s", settings.server_conf_path, exc)
        return None


def netmask_to_prefix(netmask: str) -> int | None:
    """把点分十进制掩码转成前缀长度；非法掩码（如 255.0.255.0）返回 None。"""
    try:
        net = IPv4Network(f"0.0.0.0/{netmask}", strict=False)
    except ValueError:
        return None
    # 复核：IPv4Network 会把非连续掩码放宽成合法网段，必须比对回写文本
    if net.netmask.compressed != netmask:
        return None
    return net.prefixlen


def _int_or_none(raw: str | None) -> int | None:
    """把 push 参数转成整数；超出合理范围或非数字都当"没有"。"""
    if raw is None or not raw.isdigit():
        return None
    value = int(raw)
    return value if 0 <= value <= 10485760 else None


def _route_to_cidr(net: str, mask: str | None) -> str | None:
    """``push "route <net> [<mask>]"`` -> CIDR 文本。"""
    if mask is None:
        # 单参数形式只给网络号：OpenVPN 按 /32（单主机路由）处理
        try:
            return str(ip_network(f"{net}/32", strict=False))
        except ValueError:
            return None
    try:
        return str(ip_network(f"{net}/{mask}", strict=False))
    except ValueError:
        return None


def parse_push_directives(push_directives: tuple[str, ...] | list[str]) -> dict:
    """把 push 指令分类成 DNS / 路由 / 是否重定向全部流量。

    只认三种托管形态（与助手 ``PUSH_ALLOW`` 对应）::

        dhcp-option DNS <ip>
        redirect-gateway ...
        route <net> [<mask>]

    局限（刻意为之，不臆测）：``dhcp-option DNS`` 一行的多个地址（逗号分隔）
    这里只记为一个字符串，交由上层做 IPv4/IPv6 校验；``<connection>`` 块里的
    push 不在本函数视野内——本项目不生成多 connection 配置。
    """
    dns: list[str] = []
    routes: list[str] = []
    redirect = False
    for raw in push_directives:
        directive = " ".join(str(raw).split())
        dns_match = _PUSH_DNS_RE.match(directive)
        if dns_match:
            dns.append(dns_match.group(1))
            continue
        if _PUSH_REDIRECT_RE.match(directive):
            redirect = True
            continue
        route_match = _PUSH_ROUTE_RE.match(directive)
        if route_match:
            cidr = _route_to_cidr(route_match.group(1), route_match.group(2))
            if cidr:
                routes.append(cidr)
    return {"dns_servers": dns, "routes": routes, "redirect_gateway": redirect}


def editable_settings(conf: ServerConf, pushes: dict) -> dict:
    """GET /api/settings/server 里 ``settings`` 字段的内容（可编辑项的当前值）。

    ``network`` 只给网络地址（如 ``10.8.44.0``），前缀长度放在 ``network_cidr``
    里；``netmask`` 未声明时为 None（不臆测成 255.255.255.0）。
    """
    is_ipv4 = isinstance(conf.network, IPv4Network)
    return {
        "port": conf.port,
        "proto": conf.proto,
        "dns_servers": list(pushes["dns_servers"]),
        "tunnel_mode": "full" if pushes["redirect_gateway"] else "split",
        "routes": list(pushes["routes"]),
        "block_ipv6": conf.block_ipv6,
        "mssfix": conf.mssfix,
        "sndbuf": conf.sndbuf,
        "rcvbuf": conf.rcvbuf,
        "keepalive": conf.keepalive,
        "client_to_client": conf.client_to_client,
        "max_clients": conf.max_clients,
        "duplicate_cn": conf.duplicate_cn,
        "network": str(conf.network.network_address) if is_ipv4 else None,
        "network_cidr": conf.network_cidr,
        "netmask": conf.netmask_or_default,
        "user": conf.user,
        "group": conf.group,
        "log_append": conf.log_append,
        "status_file": conf.status_file,
        # data-ciphers / auth 是可编辑项（在 EDITABLE_KEYS 与托管块里），必须出现在
        # 这里。曾经只加进了 readonly_settings，导致"能写、读不回"：面板显示空值，
        # 每次保存都被判成"已修改"。实测在 110.42.102.239 上发现。
        "data_ciphers": conf.data_ciphers,
        "auth": conf.auth,
        "topology": conf.topology if conf.topology in _VALID_TOPOLOGIES else DEFAULT_EDITABLE_TOPOLOGY,
    }


def readonly_settings(conf: ServerConf) -> dict:
    """前台只读展示项：改这些会把客户端/服务端握手改坏，因此不开放编辑。"""
    return {
        "management": conf.management,
        "status_version": conf.status_version,
        "crl_verify": conf.crl_verify,
        "client_config_dir": conf.client_config_dir,
        "tls_mode": conf.tls_mode,
        "cipher": conf.cipher,
    }


# 可编辑键 -> 期望的 Python 类型（bool 必须在 int 之前判断，见 validate_editable）
EDITABLE_KEYS: tuple[str, ...] = (
    "port",
    "proto",
    "dns_servers",
    "tunnel_mode",
    "routes",
    "block_ipv6",
    "mssfix",
    "sndbuf",
    "rcvbuf",
    "keepalive",
    "client_to_client",
    "max_clients",
    "duplicate_cn",
    "network",
    "netmask",
    "topology",
    "data_ciphers",
    "auth",
    "user",
    "group",
    "log_append",
    "status_file",
)

_NULLABLE_INTS = {"mssfix", "sndbuf", "rcvbuf", "max_clients"}


def validate_editable(payload: dict, *, current: dict) -> list[str]:
    """按接口约定严格校验 PATCH 载荷，返回中文问题列表（空列表表示通过）。

    只做校验，不做值归一化——归一化在 ``normalize_editable`` 里，
    两者分开是为了让 GET/PATCH 的"显示值"与"落盘值"能各自被单独测试。
    """
    problems: list[str] = []
    unknown = [key for key in payload if key not in EDITABLE_KEYS]
    if unknown:
        # 只读项（cipher/tls_mode/management/...）也走这条：它们确实不可编辑
        problems.append(f"不支持的设置项：{', '.join(sorted(unknown))}")

    for key, value in payload.items():
        if key not in EDITABLE_KEYS:
            continue
        if key == "port":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                problems.append("port 必须是 1-65535 之间的整数")
        elif key == "proto":
            # 只允许 udp/tcp（不开放 udp6/tcp6：IPv6 监听面在只读项里）
            if value not in ("udp", "tcp"):
                problems.append('proto 只能是 "udp" 或 "tcp"')
        elif key == "dns_servers":
            if not isinstance(value, list) or len(value) > 2:
                problems.append("dns_servers 必须是数组，且最多 2 个地址")
            else:
                for item in value:
                    if not isinstance(item, str):
                        problems.append(f"dns_servers 里的地址必须是字符串：{item!r}")
                        continue
                    try:
                        IPv4Address(item) if ":" not in item else __import__("ipaddress").IPv6Address(item)
                    except ValueError:
                        problems.append(f"dns_servers 里的地址不是合法 IPv4/IPv6：{item}")
        elif key == "tunnel_mode":
            if value not in ("full", "split"):
                problems.append('tunnel_mode 只能是 "full" 或 "split"')
        elif key == "routes":
            if not isinstance(value, list):
                problems.append("routes 必须是数组")
            else:
                for item in value:
                    if not isinstance(item, str):
                        problems.append(f"routes 里的网段必须是字符串：{item!r}")
                        continue
                    try:
                        ip_network(item, strict=False)
                    except ValueError:
                        problems.append(f"routes 里的网段不是合法 CIDR：{item}")
        elif key == "block_ipv6" or key == "client_to_client" or key == "duplicate_cn":
            if not isinstance(value, bool):
                problems.append(f"{key} 必须是 true/false")
        elif key in _NULLABLE_INTS:
            if value is None:
                pass
            elif isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{key} 必须是整数或 null")
            elif key == "mssfix" and not 0 <= value <= 1500:
                problems.append("mssfix 必须在 0-1500 之间")
            elif key != "mssfix" and not 0 <= value <= 10485760:
                problems.append(f"{key} 必须在 0-10485760 之间")
        elif key in ("log_append", "status_file"):
            # OpenVPN 以非 root 运行，只有 /var/log/openvpn 被授权写入。
            # 允许改到别处必然导致降权后写不了日志、服务起不来（虽然会回滚，
            # 但没必要让用户踩这一下）。
            if value is not None:
                if not isinstance(value, str) or not value.startswith("/var/log/openvpn/"):
                    problems.append(f"{key} 必须位于 /var/log/openvpn/ 下（OpenVPN 以降权用户运行，只有该目录被授权写入）")
                elif ".." in value or "\n" in value:
                    problems.append(f"{key} 含非法字符")
        elif key in ("user", "group"):
            # 只校验名字形态；该用户/组是否真的存在、降权后能否读证书，
            # 由助手重启服务时用真实结果回答（起不来就整体回滚）。
            if value is not None and (not isinstance(value, str) or not _USERNAME_RE.match(value)):
                problems.append(f"{key} 必须是合法的系统用户/组名（字母或下划线开头，可含数字、下划线、连字符）")
        elif key == "auth":
            if value is not None and value not in _VALID_AUTH:
                problems.append(f"auth 只能是 {'/'.join(_VALID_AUTH)} 或 null")
        elif key == "data_ciphers":
            if value is not None:
                if not isinstance(value, str):
                    problems.append("data_ciphers 必须是冒号分隔的字符串")
                else:
                    items = [x.strip() for x in value.split(":") if x.strip()]
                    if not items:
                        problems.append("data_ciphers 不能为空")
                    for item in items:
                        if item not in _VALID_CIPHERS:
                            problems.append(f"data_ciphers 含不支持的加密套件：{item}")
        elif key == "keepalive":
            if value is not None and (not isinstance(value, str) or not _KEEPALIVE_RE.match(value)):
                problems.append('keepalive 必须形如 "10 120"（两个整数）或 null')
        elif key == "network":
            if not isinstance(value, str):
                problems.append("network 必须是 IPv4 网络地址字符串")
                continue
            # 掩码：同一请求里给了就用它（稍后单独校验），否则用当前值
            netmask = payload.get("netmask", current.get("netmask")) or DEFAULT_NETMASK
            prefix = netmask_to_prefix(str(netmask))
            if prefix is None:
                problems.append(f"netmask 不是合法 IPv4 掩码：{netmask}")
                continue
            if prefix < 8:
                problems.append("network 的掩码过宽（前缀长度必须 >= 8）")
                continue
            try:
                network = ip_network(f"{value}/{prefix}", strict=False)
            except ValueError:
                problems.append(f"network 不是合法 IPv4 网络地址：{value}")
                continue
            if network.version != 4:
                problems.append("network 目前只支持 IPv4 网段")
            elif network.network_address.compressed != value.strip():
                # strict=False 会把主机位抹掉；与用户输入不一致时必须明确报错，
                # 否则面板会"悄无声息地"把 10.9.0.7/16 存成 10.9.0.0/16
                problems.append(
                    f"network 必须是网段地址：{value}/{netmask} "
                    f"会被规范化为 {network.network_address.compressed}"
                )
        elif key == "netmask":
            prefix = netmask_to_prefix(value) if isinstance(value, str) else None
            if prefix is None:
                problems.append(f"netmask 不是合法 IPv4 掩码：{value!r}")
                continue
            if prefix < 8:
                problems.append("netmask 的掩码过宽（前缀长度必须 >= 8）")
                continue
            # 只改掩码时也要复核：当前网络地址在新掩码下必须仍是网段地址
            # （10.9.0.0/16 -> /8 会变成 10.0.0.0/8，等于把网段悄悄挪走）
            current_network = payload.get("network", current.get("network"))
            if isinstance(current_network, str) and current_network:
                try:
                    composed = ip_network(f"{current_network}/{value}", strict=False)
                except ValueError:
                    problems.append(f"network 与 netmask 不匹配：{current_network}/{value}")
                else:
                    if composed.network_address.compressed != current_network:
                        problems.append(
                            f"netmask 与当前网段不匹配：{current_network}/{value} "
                            f"会被规范化为 {composed.network_address.compressed}，"
                            "请同时提交新的 network"
                        )
        elif key == "topology":
            if value not in _VALID_TOPOLOGIES:
                problems.append('topology 只能是 "subnet"、"net30" 或 "p2p"')

    # 跨字段：network 与 netmask 必须能组成同一个网段
    if "network" in payload and "netmask" in payload and isinstance(payload["network"], str):
        if isinstance(payload["netmask"], str) and netmask_to_prefix(payload["netmask"]) is not None:
            try:
                composed = ip_network(f"{payload['network']}/{payload['netmask']}", strict=False)
            except ValueError:
                problems.append("network 与 netmask 不匹配")
            else:
                if composed.network_address.compressed != payload["network"].strip():
                    problems.append(
                        f"network 必须是网段地址：{payload['network']}/{payload['netmask']} "
                        f"会被规范化为 {composed.network_address.compressed}"
                    )
    return problems


def normalize_editable(payload: dict, *, current: dict) -> dict:
    """把 PATCH 载荷变成可直接渲染的托管块取值（假定已通过 validate_editable）。

    * ``routes`` 统一规范化成 CIDR 文本；
    * ``network``/``netmask`` 统一成网络地址 + 掩码（``strict=False`` 抹掉主机位）；
    * 只返回显式出现的键——未出现的键由调用方从 ``current`` 补齐。
    """
    normalized: dict = {}
    for key in EDITABLE_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if key == "routes" and isinstance(value, list):
            normalized[key] = [str(ip_network(item, strict=False)) for item in value if isinstance(item, str)]
        elif key == "dns_servers" and isinstance(value, list):
            normalized[key] = [str(item) for item in value]
        elif key == "proto" and isinstance(value, str):
            normalized[key] = value.lower()
        elif key == "network" and isinstance(value, str):
            netmask = payload.get("netmask", current.get("netmask")) or DEFAULT_NETMASK
            normalized[key] = str(ip_network(f"{value}/{netmask}", strict=False).network_address)
        elif key == "netmask" and isinstance(value, str):
            normalized[key] = value
        elif key == "keepalive" and isinstance(value, str):
            normalized[key] = " ".join(value.split())
        else:
            normalized[key] = value
    return normalized


__all__ = [
    "ServerConf",
    "load",
    "invalidate",
    "read_text",
    "DEFAULT_TOPOLOGY",
    "DEFAULT_NETMASK",
    "DEFAULT_EDITABLE_TOPOLOGY",
    "MANAGED_BEGIN",
    "MANAGED_END",
    "MANAGED_SCALARS",
    "PUSH_ALLOW_PREFIXES",
    "BLOCK_IPV6_CLIENT_ADDRESS",
    "BLOCK_IPV6_CLIENT_REMOTE",
    "RETIRED_PREFIX",
    "EDITABLE_KEYS",
    "split_managed",
    "managed_block_present",
    "netmask_to_prefix",
    "parse_push_directives",
    "editable_settings",
    "readonly_settings",
    "validate_editable",
    "normalize_editable",
]
