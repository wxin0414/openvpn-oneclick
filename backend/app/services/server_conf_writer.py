"""server.conf 托管块的渲染与候选文件生成（纯函数，不碰 sudo、不碰 systemd）。

为什么需要这一层
----------------
面板以**非 root** 用户运行，而 ``server.conf``、NAT 规则、nginx 站点配置都归 root。
"所有设置都能在面板里改" 的前提是：面板只负责**写出它想要的配置**，再由 root
助手 ``/usr/local/lib/ovpnmanager/apply-config`` 校验并落地。于是职责被切成两半：

* 本模块（面板侧，普通用户）：渲染托管块、生成候选文件、做第一道校验；
* ``templates/apply-config.sh`` + ``templates/apply-validate.sh``（root 侧）：
  复核候选文件、备份、原子替换、按需重建 NAT/nginx、重启服务、失败回滚。

两边的白名单 **必须逐字一致**，否则面板每次保存都会被助手拒绝 —— 因此这里的
``MANAGED_DIRECTIVES`` / ``PUSH_ALLOW_PREFIXES`` / ``MANAGED_BEGIN`` /
``MANAGED_END`` 是从助手脚本里原样抄过来的契约常量，改动必须两边同时改。

候选文件的格式（关键）
----------------------
助手是 **整文件替换**：``cp "$CANDIDATE" "$tmp"; mv "$tmp" "$SERVER_CONF"``。
所以候选文件必须是一份**完整的 server.conf**，而不是只有托管块。助手另外会做
``comm -13 <(非托管行 当前配置) <(非托管行 候选)``，要求"候选不得引入当前配置里
没有的非托管指令"。这两条约束决定了 ``build_candidate`` 的形态：

1. 保留当前配置的**所有**非托管行，逐字节不动；
2. 把旧的托管块整段删掉（它会变成新的托管块）；
3. 把已经存在的托管指令行**注释掉**（面板接管的行加 ``RETIRED_PREFIX`` 前缀），
   并在文件末尾追加新的托管块；
4. 除以上三类外**不动任何一行**。

为什么要"注释掉旧行"而不是直接删掉
----------------------------------
* OpenVPN 对**标量**指令（``port``/``proto``/...) 是"后者胜出"，托管块在文件
  末尾所以永远赢 —— 但把旧行去掉更干净，也避免运维看配置时误解；
* 对 ``push`` 这类**累加**指令，旧行如果留着会和新托管块里的 push 一起生效：
  客户端会同时收到两组 DNS / 路由，出现"改完 DNS 还是旧的"这种静默故障。
  注释掉是唯一既能中和旧 push、又不违反助手"不得引入新指令"约束的写法 ——
  助手对托管块之外的注释行是直接跳过的（``/^[ \\t]*#/ {next}``），
  而 OpenVPN 同样忽略 ``#`` 开头的行。
  也正因为如此，``#`` 前缀在这里是**功能性**选择，不只是可读性。
* 下次生成候选时，带 ``RETIRED_PREFIX`` 的旧注释会被丢掉再重新生成，
  不会越攒越多（见 ``_retire_managed_lines``）。

安全边界（这是整个特性敢上线的理由）
------------------------------------
``validate_candidate`` 是最后一道"面板被攻破"防线，且**刻意与 root 助手同构**：

* 托管块里只能出现 ``MANAGED_DIRECTIVES`` 里的指令名；
* ``push`` 载荷只能匹配 ``PUSH_ALLOW_PREFIXES`` 里前缀的规定形态；
* 托管块之外不得出现当前 ``server.conf`` 里没有的指令行（助手的 ``comm -13``）。

其余一切（``script-security``、``up``、``plugin``、``tls-crypt``……）一律拒绝。
root 助手会独立地再校验一遍同样的规则，两边都不相信对方 —— 判定规则任何一处
不一致，结果不是"每次保存都失败"，就是"面板的普通用户拿到了 root 命令执行"。
没有这些规则，一个被攻破的面板就等于 root 代码执行。
"""

from __future__ import annotations

import logging
import os
import tempfile
from ipaddress import ip_network
from pathlib import Path

from app.core.config import get_settings
from app.services.server_conf import (
    BLOCK_IPV6_CLIENT_ADDRESS,
    BLOCK_IPV6_CLIENT_REMOTE,
    MANAGED_BEGIN,
    MANAGED_END,
    PUSH_ALLOW_PREFIXES,
    RETIRED_PREFIX,
    split_managed,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# 契约常量：必须与 templates/apply-validate.sh 里的 MANAGED_ALLOW 逐字一致。
# 用元组（而不是 set）是为了让"两边比对"的测试能直接按顺序对着脚本 grep。
MANAGED_DIRECTIVES: tuple[str, ...] = (
    "port",
    "proto",
    "server",
    "topology",
    "data-ciphers",
    "auth",
    "user",
    "group",
    "log-append",
    "status",
    "mssfix",
    "sndbuf",
    "rcvbuf",
    "keepalive",
    "client-to-client",
    "max-clients",
    "duplicate-cn",
    "block-ipv6",
    "push",
    "fast-io",
    "explicit-exit-notify",
)
MANAGED_DIRECTIVE_SET: frozenset[str] = frozenset(MANAGED_DIRECTIVES)

# 没有参数的布尔型指令：注释/识别时按"整行等于指令名或后跟空格"处理。
FLAG_DIRECTIVES: frozenset[str] = frozenset(
    {"client-to-client", "duplicate-cn", "block-ipv6", "fast-io", "explicit-exit-notify"}
)

# 面板托管的标量（不含 push，用于"旧行接管"）；其余托管指令以 push 处理。
MANAGED_SCALARS: frozenset[str] = frozenset(MANAGED_DIRECTIVE_SET - {"push"})

PUSH_ALLOW: tuple[str, ...] = tuple(PUSH_ALLOW_PREFIXES)

# 渲染顺序（固定，保证同一份设置每次生成的块逐字节一致）。
# 「先标量、后 push」不是审美问题：助手/OpenVPN 都按"最后出现的标量胜出"解析，
# 把 push 放在末尾可以让运维一眼看出客户端被推了什么。
RENDER_ORDER: tuple[str, ...] = (
    "port",
    "proto",
    "fast-io",
    "explicit-exit-notify",
    "server",
    "topology",
    "data-ciphers",
    "auth",
    "user",
    "group",
    "log-append",
    "status",
    "mssfix",
    "sndbuf",
    "rcvbuf",
    "keepalive",
    "client-to-client",
    "max-clients",
    "duplicate-cn",
    "block-ipv6",
    "push",
)

# 特权助手路径。config.py 不允许改动，因此按环境变量读取（部署时写进 .env），
# 默认值与 templates/apply.conf.template / sudoers 模板里的路径一致。
#
# 安全提醒：这个值**只来自面板自己的环境**（helper 的文件头注释已经写明：
# 路径固定、sudoers 只授权这一条命令且不接受任何参数）。不要把它做成接口参数，
# 否则就等于把"以 root 执行任意程序"的能力交出去了。
APPLY_CONFIG_BIN: str = os.environ.get("APPLY_CONFIG_BIN", "/usr/local/lib/ovpnmanager/apply-config")
# 助手执行超时：包含"重启服务 + 轮询 is-active（默认 20s）"，这里留足余量。
APPLY_TIMEOUT_SECONDS: int = int(os.environ.get("APPLY_CONFIG_TIMEOUT", "90"))

CANDIDATE_FILENAME = "server.conf.candidate"


def candidate_path() -> Path:
    """候选文件的固定路径（面板数据目录，归面板用户所有）。

    ``sqlite_path`` 是 ``<panel_home>/backend/data/app.db``，因此它的父目录就是
    面板自己的数据目录。放在这里既是助手 apply.conf 里 CANDIDATE 的默认位置，
    也满足助手的两条校验：属主是面板用户、组/其他不可写（我们按 0600 写）。
    """
    return Path(settings.sqlite_path).parent / CANDIDATE_FILENAME


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _as_prefix(value) -> str | None:
    """把掩码或前缀长度统一成 ``"24"`` 形式；无法识别时返回 None。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if 0 <= value <= 32 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return text if 0 <= int(text) <= 32 else None
        try:
            # 非连续掩码（如 255.0.255.0）会被 ip_network 放宽，必须复核回写文本
            net = ip_network(f"0.0.0.0/{text}", strict=False)
        except ValueError:
            return None
        if str(net.netmask) != text:
            return None
        return str(net.prefixlen)
    return None


def _push(payload: str) -> str:
    """生成一行 push（始终带引号，与 install.sh 生成的格式一致）。"""
    return f'push "{payload}"'


def render_managed_block(settings_dict: dict) -> list[str]:
    """把设置字典渲染成托管块内的指令行（**不含**标记行）。

    参数是 ``GET /api/settings/server`` 里 ``settings`` 的那套键。允许传入部分
    键：没有的项就不生成对应指令（例如只改 ``port`` 时，调用方会先用当前配置
    补全再传进来 —— 见 ``app/api/api_v1/endpoints/settings.py``）。

    只生成助手白名单允许的形态，绝不生成 ``push "xxx"`` 之外的花样：

    * ``fast-io`` / ``explicit-exit-notify 1`` 只对 UDP 有效，proto 为 tcp 时
      两者都不生成。这是必须的：否则在面板里把 proto 从 udp 改成 tcp 之后，
      配置里会留下过期的 UDP 专用指令（install.sh 里已经修过一次同类问题）。
    * ``block_ipv6`` 为真时生成**三行**（``push "ifconfig-ipv6 ..."``、
      ``push "redirect-gateway ipv6"``、``block-ipv6``）：只写服务端指令挡不住
      双栈客户端——它的 IPv6 默认路由还在网卡上，流量根本不进隧道。
    """
    lines: list[str] = []

    if settings_dict.get("port") is not None:
        lines.append(f"port {int(settings_dict['port'])}")
    proto = str(settings_dict.get("proto") or "udp").lower()
    lines.append(f"proto {proto}")
    if proto == "udp":
        # UDP 专用：proto=tcp 时不出现，避免留下过期指令
        lines.append("fast-io")
        lines.append("explicit-exit-notify 1")

    network = settings_dict.get("network")
    if network:
        prefix = _as_prefix(settings_dict.get("netmask")) or "24"
        # server 指令必须是网络地址 + 掩码两个参数（OpenVPN 不接受 CIDR 写法）
        lines.append(f"server {network} {ip_network(f'{network}/{prefix}', strict=False).netmask}")
    if settings_dict.get("topology"):
        lines.append(f"topology {settings_dict['topology']}")

    for key in ("mssfix", "sndbuf", "rcvbuf"):
        value = settings_dict.get(key)
        if value is not None and value != "":
            lines.append(f"{key} {int(value)}")
    # 加密套件与认证摘要：字符串，原样写入；取值已在 validate_editable 里
    # 按白名单校验过，这里不重复判断。
    if settings_dict.get("data_ciphers"):
        lines.append(f"data-ciphers {settings_dict['data_ciphers']}")
    if settings_dict.get("auth"):
        lines.append(f"auth {settings_dict['auth']}")
    # 降权运行：OpenVPN 启动完成后切到这些用户/组。名字由 validate_editable
    # 按用户名的字符规则校验；若该用户不存在或降权后读不到证书，服务会起不来，
    # 助手会整体回滚（见 apply-config 的 ROLLED_BACK 分支）。
    for key in ("user", "group"):
        value = settings_dict.get(key)
        if value:
            lines.append(f"{key} {value}")
    # 日志与状态文件：路径已由 validate_editable 限定在 /var/log/openvpn/ 下，
    # 那里安装脚本已 chown 给 OpenVPN 的运行用户，所以降权后仍可写。
    if settings_dict.get("log_append"):
        lines.append(f"log-append {settings_dict['log_append']}")
    if settings_dict.get("status_file"):
        lines.append(f"status {settings_dict['status_file']}")
    # 收发缓冲还必须**推给客户端**：只调服务端一侧的话客户端仍然是小缓冲，
    # 吞吐收益主要来自两端一起放大。install.sh 写入的托管块里本来就有这两条 push，
    # 面板若不输出，用户保存一次设置后客户端就再也拿不到缓冲调优（静默回退）。
    for key in ("sndbuf", "rcvbuf"):
        value = settings_dict.get(key)
        if value is not None and value != "":
            lines.append(_push(f"{key} {int(value)}"))
    if settings_dict.get("keepalive"):
        lines.append(f"keepalive {' '.join(str(settings_dict['keepalive']).split())}")
    if settings_dict.get("client_to_client"):
        lines.append("client-to-client")
    if settings_dict.get("max_clients") is not None:
        lines.append(f"max-clients {int(settings_dict['max_clients'])}")
    if settings_dict.get("duplicate_cn"):
        lines.append("duplicate-cn")

    # --- push（会累加，顺序固定，形态受限） ---
    if settings_dict.get("tunnel_mode") == "full":
        # bypass-dhcp 只对 Windows 客户端有效，其它平台忽略该标志
        lines.append(_push("redirect-gateway def1 bypass-dhcp"))
    else:
        for route in settings_dict.get("routes") or []:
            cidr = str(ip_network(str(route), strict=False))
            # 助手只接受 "route <net> <mask>" 两点分十进制形态，不接受 CIDR
            net = ip_network(cidr)
            lines.append(_push(f"route {net.network_address} {net.netmask}"))

    if settings_dict.get("block_ipv6"):
        # 只写服务端的 ``block-ipv6`` 是不够的：双栈客户端的 IPv6 默认路由仍在
        # 网卡上，它的 IPv6 流量根本不会进隧道，服务端的 block-ipv6 自然看不到。
        # OpenVPN 手册（--block-ipv6）给的服务端写法是这三行一起：
        #   先给客户端一个 ULA 地址并把它所有 IPv6 流量拉进隧道，
        #   再由服务端的 block-ipv6 用 ICMPv6 no-route 回掉。
        # 这与 install.sh 写入的形态完全一致（面板不能和安装脚本写出两种配置）。
        lines.append(_push(f"ifconfig-ipv6 {BLOCK_IPV6_CLIENT_ADDRESS} {BLOCK_IPV6_CLIENT_REMOTE}"))
        lines.append(_push("redirect-gateway ipv6"))
        lines.append("block-ipv6")

    for server in settings_dict.get("dns_servers") or []:
        lines.append(_push(f"dhcp-option DNS {server}"))

    return lines


# --------------------------------------------------------------------------- #
# 候选文件
# --------------------------------------------------------------------------- #
def _retire_managed_lines(lines: list[str]) -> list[str]:
    """把旧的托管指令行注释掉（幂等）。

    ``port 1194`` -> ``# [ovpn-oneclick retired] port 1194``

    幂等性怎么保证：**已经带 RETIRED_PREFIX 的行原样保留、不重新生成**。
    重新生成（先把旧注释丢掉再按当前内容重排）会让"第一次保存"和"第二次保存"
    产生不同字节数的文件，而这正是配置在每次保存后悄悄变长/变位的来源。
    原样保留则保证 ``build_candidate(build_candidate(x)) == build_candidate(x)``，
    代价是历史行会一直留在文件里 —— 它们是注释，对 OpenVPN 和 root 助手都无效，
    而且正好充当"这行原来是什么"的现场记录。
    """
    retired: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(RETIRED_PREFIX):
            retired.append(line)  # 已经注释过的历史行：原样保留（幂等）
            continue
        name = stripped.split(None, 1)[0].lower() if stripped else ""
        if name == "push":
            # 只接管"面板托管形态"的 push（DNS/redirect-gateway/route），
            # 其它 push（例如用户手写的 push "block-outside-dns"）原样保留：
            # 面板没有能力表达它们，注释掉等于替用户删配置。
            payload = stripped.split(None, 1)[1].strip().strip('"') if " " in stripped else ""
            first = payload.split(" ", 1)[0] if payload else ""
            if first in PUSH_ALLOW:
                retired.append(f"{RETIRED_PREFIX}{stripped}")
            else:
                retired.append(line)
            continue
        if name in MANAGED_SCALARS:
            # 布尔型指令只有 ``client-to-client`` 或 ``client-to-client `` 两种形态，
            # 用"整行等于指令名或以它加空格开头"判断，避免误伤 ``fast-iox`` 这类
            # 前缀相同的无关指令。
            if name in FLAG_DIRECTIVES and stripped != name and not stripped.startswith(f"{name} "):
                retired.append(line)
                continue
            retired.append(f"{RETIRED_PREFIX}{stripped}")
            continue
        retired.append(line)
    return retired


def build_candidate(current_conf_text: str, settings_dict: dict) -> str:
    """生成完整候选配置文本（可直接被 root 助手整文件替换）。

    步骤见模块头部说明。返回的文本以换行结尾；空行保留但不会在末尾堆叠。

    幂等：``build_candidate(build_candidate(text, s), s) == build_candidate(text, s)``。
    用户连续保存两次不会让文件继续变化（回归保护见 tests/test_fixes.py 的 J29）。
    """
    before, _managed, after = split_managed(current_conf_text or "")
    # 块前 + 块后：保留当前配置的所有非托管行，同时丢掉旧托管块
    lines = _retire_managed_lines(before + after)
    block = render_managed_block(settings_dict)

    parts: list[str] = list(lines)
    # 追加前把末尾空行压成一个，避免每保存一次就多一个空行
    while parts and not parts[-1].strip():
        parts.pop()
    if parts:
        parts.append("")
    parts.append(MANAGED_BEGIN)
    parts.extend(block)
    parts.append(MANAGED_END)
    return "\n".join(parts) + "\n"


def write_candidate(path: Path | str, text: str) -> Path:
    """原子地写候选文件，权限 0600。

    与 ``certs._publish_crl`` 同一个思路：面板写出去的文件必须显式设定权限，
    不能跟随 umask。这里的 0600 不是"顺手加固"，而是助手的硬性要求——
    ``apply-validate.sh`` 会拒绝组/其他可写的候选文件（防止本机其它用户投毒），
    而 0600 同时挡住了"其它用户读到候选内容"。

    ``os.replace`` 保证助手永远看不到半截文件；临时文件建在同一目录，
    避免跨文件系统 rename 失败。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        tmp.chmod(0o600)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


# --------------------------------------------------------------------------- #
# 校验（面板侧的安全网；root 助手会独立再校验一遍）
# --------------------------------------------------------------------------- #
def _push_problems(payload: str) -> list[str]:
    """校验一条 push 载荷；返回中文问题列表（空表示通过）。"""
    problems: list[str] = []
    parts = payload.split()
    if not parts:
        return ["push 指令内容为空"]
    first = parts[0]
    if first not in PUSH_ALLOW:
        return [f"push 载荷不被允许（首个词必须是 {'/'.join(PUSH_ALLOW)} 之一）：{payload}"]

    if first == "dhcp-option":
        if len(parts) < 3 or parts[1] != "DNS":
            return [f"push dhcp-option 只允许 DNS 形式（dhcp-option DNS <地址>）：{payload}"]
    elif first == "sndbuf" or first == "rcvbuf":
        if len(parts) != 2 or not parts[1].isdigit():
            problems.append(f"push {first} 只允许跟一个整数：{payload}")
    elif first == "route":
        if len(parts) == 2:
            try:
                ip_network(parts[1], strict=False)
            except ValueError:
                problems.append(f"push route 的网段非法：{payload}")
        elif len(parts) == 3:
            try:
                ip_network(f"{parts[1]}/{parts[2]}", strict=False)
            except ValueError:
                problems.append(f"push route 的参数非法：{payload}")
        else:
            problems.append(f"push route 只允许 route <net> [<mask>]：{payload}")
    elif first == "ifconfig-ipv6":
        # 形如 ``<addr>/<prefix> [<remote>]``，例如 fd15:53b6:dead::2/64 fd15:53b6:dead::1
        if len(parts) not in (2, 3):
            problems.append(f"push ifconfig-ipv6 只允许 <地址/前缀> [<对端地址>]：{payload}")
        else:
            try:
                ip_network(parts[1], strict=False)
            except ValueError:
                problems.append(f"push ifconfig-ipv6 的地址非法：{payload}")
            if len(parts) == 3:
                try:
                    ip_network(f"{parts[2]}/128", strict=False)
                except ValueError:
                    problems.append(f"push ifconfig-ipv6 的对端地址非法：{payload}")
    elif first == "redirect-gateway":
        # 助手只接受字母/数字/!/空格/连字符
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789! -"
        arguments = " ".join(parts[1:])
        if any(char not in allowed for char in arguments):
            problems.append(f"push redirect-gateway 的参数不被允许：{payload}")
    return problems


def _nonmanaged_lines(text: str) -> set[str]:
    """托管块之外、去掉注释与空行后的指令行（去首尾空白、去重）。

    与 root 助手 ``apply-validate.sh::nonmanaged_lines()`` 的 awk 规则一一对应：
    ``$0==b``/``$0==e`` 逐字符判定标记、跳过注释行与空行、只对行首尾空白做修剪。
    注意"跳过注释"正是整个特性成立的关键：面板把旧指令注释成
    ``# [ovpn-oneclick retired] port 1194`` 之后，这些行既不在这个集合里，
    也不会被助手判成"候选引入了新指令"。
    """
    before, _managed, after = split_managed(text)
    result: set[str] = set()
    for raw in list(before) + list(after):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        result.add(stripped)
    return result


def candidate_introduces_new_directives(text: str, current_text: str) -> list[str]:
    """候选在托管块之外**新增**的指令行（对应助手的 ``comm -13``）。"""
    return sorted(_nonmanaged_lines(text) - _nonmanaged_lines(current_text))


def validate_candidate(text: str, *, current_text: str | None = None) -> list[str]:
    """校验候选配置；返回中文问题列表（空列表 = 可以交给 root 助手）。

    这是"面板被攻破"时的最后一道防线，规则刻意保守：

    1. 标记必须成对出现、各一次、顺序正确；
    2. 托管块内的指令名必须在 ``MANAGED_DIRECTIVES`` 里；
    3. ``push`` 载荷必须匹配 ``PUSH_ALLOW`` 里各前缀的规定形态；
    4. 给了 ``current_text`` 时，托管块**之外**不允许出现当前配置里没有的指令行
       —— 与助手 ``comm -13 <(nonmanaged 当前) <(nonmanaged 候选)`` 完全一致。

    第 2、4 条挡住的是最危险的一类攻击：把 ``script-security 2`` + ``up /tmp/x.sh``
    混进候选，再由 root 助手原样落到 server.conf —— 那就等于把面板的普通用户
    权限直接升级成 root 命令执行。第 3 条挡住"用 push 夹带"这条路。

    ``current_text`` 省略时只做 1~3 条（无法判断第 4 条）。接口层必须传入当前
    配置，否则"面板自检通过、助手拒绝"会让每次保存都失败。
    """
    problems: list[str] = []
    if not text.strip():
        return ["候选配置为空"]

    lines = text.splitlines()
    begins = [index for index, line in enumerate(lines) if line.strip() == MANAGED_BEGIN]
    ends = [index for index, line in enumerate(lines) if line.strip() == MANAGED_END]
    if len(begins) != 1 or len(ends) != 1:
        return [f"托管块标记必须各出现一次（找到开始 {len(begins)} 次、结束 {len(ends)} 次）"]
    if begins[0] > ends[0]:
        return ["托管块标记顺序错误（结束标记在开始标记之前）"]

    _before, managed, _after = split_managed(text)
    if not [line for line in managed if line.strip() and not line.strip().startswith(("#", ";"))]:
        problems.append("托管块内没有任何指令")

    if current_text is not None:
        for extra in candidate_introduces_new_directives(text, current_text)[:5]:
            problems.append(f"托管块之外不允许引入当前配置里没有的指令：{extra}")

    for raw in managed:
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        parts = stripped.split(None, 1)
        name = parts[0].lower()
        if name not in MANAGED_DIRECTIVE_SET:
            problems.append(f"托管块里出现未授权指令：{name}")
            continue
        if name == "push":
            if len(parts) < 2:
                problems.append("push 指令缺少内容")
                continue
            payload = parts[1].strip()
            if payload.startswith('"') and payload.endswith('"') and len(payload) >= 2:
                payload = payload[1:-1]
            problems.extend(_push_problems(payload))
    return problems


__all__ = [
    "APPLY_CONFIG_BIN",
    "APPLY_TIMEOUT_SECONDS",
    "BLOCK_IPV6_CLIENT_ADDRESS",
    "BLOCK_IPV6_CLIENT_REMOTE",
    "CANDIDATE_FILENAME",
    "FLAG_DIRECTIVES",
    "MANAGED_BEGIN",
    "MANAGED_DIRECTIVES",
    "MANAGED_DIRECTIVE_SET",
    "MANAGED_END",
    "MANAGED_SCALARS",
    "PUSH_ALLOW",
    "RENDER_ORDER",
    "build_candidate",
    "candidate_introduces_new_directives",
    "candidate_path",
    "render_managed_block",
    "validate_candidate",
    "write_candidate",
]
