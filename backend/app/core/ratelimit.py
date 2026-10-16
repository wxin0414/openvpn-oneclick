"""极简进程内登录限速。

面板是单实例管理端，进程内滑动窗口足够抵御在线暴力破解；重启即清零，
不引入额外依赖。多实例部署时应换成 Redis 之类的共享存储。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.config import get_settings

settings = get_settings()

_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()
_locked_until: dict[str, float] = {}


def _key(username: str, client_ip: str | None) -> str:
    return f"{(username or '').strip().lower()}@{(client_ip or 'unknown')}"


def check(key_username: str, client_ip: str | None) -> float:
    """返回还需等待的秒数；0 表示可以尝试登录。"""
    key = _key(key_username, client_ip)
    now = time.monotonic()
    with _lock:
        until = _locked_until.get(key, 0.0)
        if until > now:
            return until - now
        if until:
            _locked_until.pop(key, None)
        window = _attempts[key]
        while window and now - window[0] > settings.login_window_seconds:
            window.popleft()
    return 0.0


def register_failure(key_username: str, client_ip: str | None) -> None:
    key = _key(key_username, client_ip)
    now = time.monotonic()
    with _lock:
        window = _attempts[key]
        window.append(now)
        while window and now - window[0] > settings.login_window_seconds:
            window.popleft()
        if len(window) >= settings.login_max_attempts:
            _locked_until[key] = now + settings.login_lockout_seconds
            window.clear()


def register_success(key_username: str, client_ip: str | None) -> None:
    key = _key(key_username, client_ip)
    with _lock:
        _attempts.pop(key, None)
        _locked_until.pop(key, None)


__all__ = ["check", "register_failure", "register_success"]
