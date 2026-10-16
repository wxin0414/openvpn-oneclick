"""OpenVPN management 接口客户端（连接复用 + TTL 缓存 + 熔断）。

针对原实现的速度问题，这里做了四件事：

1) **连接复用**：原实现每条命令都 ``socket.create_connection`` 并重读 banner，
   仪表盘一次请求要建立 4 条连接。现在复用一条长连接（带锁，management 本身
   就是单线程协议）。
2) **结果合并**：``status 3`` 抓取一次同时供「在线列表」和「状态详情」使用。
   原实现把同一条 ``status 3`` 跑了两遍（``list_online`` 一次、``status_details`` 一次）。
3) **TTL 缓存**：同一页面内的重复探测被合并，默认 3 秒内复用结果。
4) **熔断**：management 不可达时快速失败，避免 4 × 3s 串行超时把一次请求拖到 12s。

另外响应改为按行解析到明确的结束标记，不再用 ``b"END" in chunk`` 这种可能
在数据中间误判、导致响应被截断的判断。
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T")

# management 接口的结束标记
_TERMINATORS = ("END",)
_PREFIX_TERMINATORS = ("SUCCESS:", "ERROR:")


class ManagementUnavailable(RuntimeError):
    """management 接口当前不可用（连接失败、超时或处于熔断期）。"""


@dataclass
class OnlineClient:
    """在线客户端信息（来自 management 或 status 文件）。"""

    common_name: str
    real_address: str
    virtual_address: str
    bytes_received: int
    bytes_sent: int
    connected_since: str
    client_id: str | None = None


@dataclass
class RoutingEntry:
    """路由表条目（来自 status 3 输出）。"""

    virtual_address: str
    real_address: str
    last_ref: str
    common_name: str


@dataclass
class StatusDetails:
    """状态详情：标题、时间、全局指标、路由表与在线客户端。"""

    title: str | None
    time_unix: int | None
    time_str: str | None
    global_stats: dict[str, int | str]
    routing_table: list[RoutingEntry]
    clients: list[OnlineClient] = field(default_factory=list)


@dataclass
class LoadStats:
    """load-stats 汇总：在线数、流量、运行时长。"""

    nclients: int | None
    bytes_in: int | None
    bytes_out: int | None
    uptime: int | None


@dataclass
class StateEvent:
    """连接状态事件（state 输出）。"""

    timestamp: int | None
    state: str
    detail: str | None
    virtual_address: str | None
    common_name: str | None
    real_address: str | None


# --------------------------------------------------------------------------- #
# TTL 缓存与熔断
# --------------------------------------------------------------------------- #
class _TTLCache:
    """极简 TTL 缓存；生产者为 Exception 时不写入缓存。"""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get_or_call(self, key: str, producer: Callable[[], T], *, force: bool = False) -> T:
        now = time.monotonic()
        if not force:
            with self._lock:
                hit = self._store.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = producer()
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


class _CircuitBreaker:
    """management 不可用时短路，避免每个请求都付出一次 socket 超时。"""

    def __init__(self, cooldown: float) -> None:
        self._cooldown = cooldown
        self._until = 0.0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            remaining = self._until - time.monotonic()
        if remaining > 0:
            raise ManagementUnavailable(f"management 接口熔断中，{remaining:.1f}s 后重试")

    def trip(self, reason: str) -> None:
        with self._lock:
            self._until = time.monotonic() + self._cooldown
        logger.warning("management 接口熔断 %.1fs：%s", self._cooldown, reason)

    def reset(self) -> None:
        with self._lock:
            self._until = 0.0


_cache = _TTLCache(settings.openvpn_status_cache_ttl)
_breaker = _CircuitBreaker(settings.openvpn_management_cooldown)


# --------------------------------------------------------------------------- #
# management 长连接
# --------------------------------------------------------------------------- #
def _normalize_terminator(line: str) -> str:
    # management 在每个响应后会打印提示符 ">"，可能紧跟最后一行
    return line.strip().rstrip(">").strip()


def _clean_line(raw: bytes) -> str:
    """解码一行并去掉 management 的提示符前缀。

    提示符 ">" 常常紧贴在下一条响应的第一个数据行前面（两者之间没有换行），
    例如 ``>SUCCESS: nclients=1`` 或 ``>CLIENT_LIST,...``。如果不剥掉这个前缀：
      * 该行无法被识别为 SUCCESS/END 结束标记，客户端会一直读到 socket 超时；
      * ``state`` 的第一条事件会被 ``startswith(">")`` 过滤掉，导致漏数据。
    """
    line = raw.decode("utf-8", "replace").rstrip("\r")
    if line.startswith(">"):
        line = line.lstrip(">")
    return line


def _is_terminator(line: str) -> bool:
    normalized = _normalize_terminator(line)
    return normalized in _TERMINATORS or normalized.startswith(_PREFIX_TERMINATORS)


class _ManagementConnection:
    """单条复用的 management 连接。线程安全。"""

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        # management 会在每个响应后打印提示符 ">"，可能紧跟最后一行且不带换行。
        # 保留未消费的残余字节，供下一条命令继续解析，避免提示符污染下一次响应。
        self._pending = b""

    # -- 内部 ---------------------------------------------------------- #
    def _drain_banner(self) -> None:
        assert self._sock is not None
        # banner 以提示符 ">" 结束；最多等一个超时周期
        deadline = time.monotonic() + settings.openvpn_management_timeout
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if buf.rstrip().endswith(b">"):
                break
            if b">" in buf:
                break
        # 提示符之后可能已经带着下一条数据；保留下来交给 _read_response
        self._pending = buf.rsplit(b">", 1)[-1] if b">" in buf else b""

    def _connect(self) -> None:
        self._sock = socket.create_connection(
            (settings.openvpn_management_host, settings.openvpn_management_port),
            timeout=settings.openvpn_management_timeout,
        )
        self._sock.settimeout(settings.openvpn_management_timeout)
        self._drain_banner()

    def _reset(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._pending = b""

    def _read_response(self) -> list[str]:
        assert self._sock is not None
        lines: list[str] = []
        buf = self._pending
        self._pending = b""
        while True:
            # 先消费已缓冲的完整行，再决定是否需要继续读 socket，
            # 这样跨 TCP 分段的响应也能正确组包。
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = _clean_line(raw)
                if not line:
                    continue
                lines.append(line)
                if _is_terminator(line):
                    self._pending = buf
                    return lines
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if buf:
            tail = _clean_line(buf)
            if tail:
                lines.append(tail)
        return lines

    # -- 对外 ---------------------------------------------------------- #
    def command(self, cmd: str) -> list[str]:
        with self._lock:
            last_error: Exception | None = None
            for attempt in (1, 2):
                try:
                    if self._sock is None:
                        self._connect()
                    assert self._sock is not None
                    self._sock.sendall((cmd + "\n").encode())
                    lines = self._read_response()
                    _breaker.reset()
                    return lines
                except (OSError, socket.timeout, AssertionError) as exc:
                    last_error = exc
                    self._reset()
                    if attempt == 2:
                        break
            reason = f"{cmd!r} 失败：{last_error}"
            _breaker.trip(reason)
            raise ManagementUnavailable(reason) from last_error

    def close(self) -> None:
        with self._lock:
            self._reset()


_connection = _ManagementConnection()


def _send_command(cmd: str) -> list[str]:
    """向 management 接口发送命令并返回行列表（复用长连接 + 熔断保护）。"""
    _breaker.check()
    return _connection.command(cmd)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _split(line: str) -> list[str]:
    """status/state 输出既可能是制表符分隔，也可能是逗号分隔。"""
    return line.split("\t") if "\t" in line else line.split(",")


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_client_lines(lines: list[str]) -> list[OnlineClient]:
    """解析 CLIENT_LIST 行（management 或 status 文件）。"""
    clients: list[OnlineClient] = []
    for line in lines:
        if not line.startswith("CLIENT_LIST"):
            continue
        parts = _split(line)
        if len(parts) < 3:
            continue
        clients.append(
            OnlineClient(
                common_name=parts[1],
                real_address=parts[2] if len(parts) > 2 else "",
                virtual_address=parts[3] if len(parts) > 3 else "",
                bytes_received=_to_int(parts[5]) if len(parts) > 5 else 0,
                bytes_sent=_to_int(parts[6]) if len(parts) > 6 else 0,
                connected_since=parts[7] if len(parts) > 7 else "",
                client_id=parts[10] if len(parts) > 10 and parts[10] else None,
            )
        )
    return clients


def _parse_status_lines(lines: list[str]) -> StatusDetails:
    """从一次 ``status 3`` 输出中同时解析在线列表、全局指标与路由表。"""
    title: str | None = None
    time_unix: int | None = None
    time_str: str | None = None
    global_stats: dict[str, int | str] = {}
    routing_table: list[RoutingEntry] = []

    for line in lines:
        if line.startswith("TITLE"):
            parts = _split(line)
            if len(parts) >= 2:
                title = ",".join(parts[1:]).strip()
        elif line.startswith("TIME"):
            parts = _split(line)
            if len(parts) >= 3:
                try:
                    time_unix = int(parts[1])
                except ValueError:
                    time_unix = None
                time_str = parts[2]
        elif line.startswith("GLOBAL_STATS"):
            parts = _split(line)
            if len(parts) >= 3:
                key, raw = parts[1], parts[2]
                try:
                    global_stats[key] = int(raw)
                except ValueError:
                    global_stats[key] = raw
        elif line.startswith("ROUTING_TABLE"):
            parts = _split(line)
            # ROUTING_TABLE,10.8.0.2,client/1.2.3.4:5678,1700000000,client1
            if len(parts) >= 5:
                routing_table.append(
                    RoutingEntry(
                        virtual_address=parts[1],
                        real_address=parts[2],
                        last_ref=parts[3],
                        common_name=parts[4],
                    )
                )

    return StatusDetails(
        title=title,
        time_unix=time_unix,
        time_str=time_str,
        global_stats=global_stats,
        routing_table=routing_table,
        clients=_parse_client_lines(lines),
    )


def _parse_status_file(path: Path) -> list[OnlineClient]:
    """兜底：无法访问 management 时解析 status 文件。"""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return _parse_client_lines(lines)


# --------------------------------------------------------------------------- #
# 对外的只读查询（带缓存）
# --------------------------------------------------------------------------- #
def status_details(*, force: bool = False) -> StatusDetails:
    """解析 ``status 3``：标题、时间、全局指标、路由表与在线客户端。"""
    return _cache.get_or_call("status", lambda: _parse_status_lines(_send_command("status 3")), force=force)


def list_online(*, force: bool = False) -> list[OnlineClient]:
    """获取在线客户端：优先 management，失败则回退到 status 文件。"""
    try:
        clients = status_details(force=force).clients
        if clients:
            return clients
    except ManagementUnavailable:
        logger.debug("management 不可用，回退读取 status 文件", exc_info=True)
    except Exception:  # noqa: BLE001 - 任何解析异常都回退，面板不应因此 500
        logger.exception("解析 management status 失败，回退读取 status 文件")
    return _parse_status_file(settings.openvpn_status_path)


def load_stats(*, force: bool = False) -> LoadStats | None:
    """``load-stats`` 汇总；服务端不支持时返回 None（原实现抛异常）。"""

    def _produce() -> LoadStats | None:
        for line in _send_command("load-stats"):
            if "SUCCESS" not in line:
                continue
            payload = line.split("SUCCESS:", 1)[1].strip()
            data: dict[str, int | None] = {"nclients": None, "bytesin": None, "bytesout": None, "uptime": None}
            for part in (p.strip() for p in payload.split(",") if "=" in p):
                key, _, value = part.partition("=")
                try:
                    data[key] = int(value)
                except ValueError:
                    data[key] = None
            return LoadStats(
                nclients=data.get("nclients"),
                bytes_in=data.get("bytesin"),
                bytes_out=data.get("bytesout"),
                uptime=data.get("uptime"),
            )
        return None

    try:
        return _cache.get_or_call("load-stats", _produce, force=force)
    except ManagementUnavailable:
        logger.debug("load-stats 不可用", exc_info=True)
        return None


def state_history(limit: int = 10, *, force: bool = False) -> list[StateEvent]:
    """最近的连接状态事件。

    注意：management 的 ``state`` 命令会返回**完整**历史，协议层无法只取尾部，
    因此这里缓存整份解析结果再切片，避免每次打开仪表盘都重复拉取与解析。
    """

    def _produce() -> list[StateEvent]:
        events: list[StateEvent] = []
        for line in _send_command("state"):
            if not line or line.startswith(("END", "SUCCESS", "ERROR", ">")):
                continue
            parts = _split(line)
            if len(parts) < 2:
                continue
            try:
                ts: int | None = int(parts[0])
            except ValueError:
                ts = None
            events.append(
                StateEvent(
                    timestamp=ts,
                    state=parts[1],
                    detail=parts[2] if len(parts) > 2 else None,
                    virtual_address=parts[3] if len(parts) > 3 else None,
                    common_name=parts[4] if len(parts) > 4 else None,
                    real_address=parts[5] if len(parts) > 5 else None,
                )
            )
        return events

    try:
        events = _cache.get_or_call("state", _produce, force=force)
    except ManagementUnavailable:
        logger.debug("state 历史不可用", exc_info=True)
        return []
    return events[:limit]


def disconnect(common_name: str) -> str:
    """按证书 CN 踢下线单个客户端（只影响该客户端）。"""
    lines = _send_command(f"kill {common_name}")
    if any("SUCCESS" in line for line in lines):
        # 在线列表已过期，立即失效缓存
        _cache.invalidate("status")
        return "disconnected"
    raise RuntimeError("Disconnect failed: " + ";".join(lines))


def health() -> dict[str, Any]:
    """诊断用：management 是否可达、是否处于熔断、连接是否已建立。"""
    detail: dict[str, Any] = {
        "host": settings.openvpn_management_host,
        "port": settings.openvpn_management_port,
    }
    try:
        lines = _send_command("version")
        detail["reachable"] = True
        detail["version"] = next((ln for ln in lines if ln.strip()), None)
    except Exception as exc:  # noqa: BLE001 - 健康探测不应抛出
        detail["reachable"] = False
        detail["error"] = str(exc)
    detail["connected"] = _connection._sock is not None  # noqa: SLF001 - 诊断用途
    return detail


def invalidate_cache() -> None:
    """外部（如服务重启、强制刷新）调用，丢弃所有缓存结果。"""
    _cache.invalidate()
