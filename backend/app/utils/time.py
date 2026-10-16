"""时间工具。

注意时间基准：数据库里 ``created_at`` 存的是 Asia/Shanghai 的 naive 时间，
而证书的 ``not_before``/``not_after`` 来自 openssl，是 **UTC** naive 时间。
两者不可混比，所以这里显式提供 ``utcnow_naive()`` 用于证书时间比较。
"""

from datetime import datetime, timedelta, timezone

try:  # 容器/精简镜像里可能没有系统 tzdata
    from zoneinfo import ZoneInfo

    SHANGHAI_TZ: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001 - 退化为固定的 UTC+8
    # 中国自 1991 年起不再使用夏令时，固定偏移与 Asia/Shanghai 等价。
    # 这里不抛异常：时区数据缺失不应导致整个面板无法启动。
    SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def now_shanghai() -> datetime:
    """Return timezone-aware datetime in Asia/Shanghai."""
    return datetime.now(tz=SHANGHAI_TZ)


def now_shanghai_naive() -> datetime:
    """Return naive datetime representing Asia/Shanghai local time (for DB storage without tz)."""
    return now_shanghai().replace(tzinfo=None)


def utcnow_naive() -> datetime:
    """返回 naive UTC 时间，用于与 openssl 解析出的证书时间比较。

    取代已废弃的 ``datetime.utcnow()``（Python 3.12 起会告警）。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["SHANGHAI_TZ", "now_shanghai", "now_shanghai_naive", "utcnow_naive"]
