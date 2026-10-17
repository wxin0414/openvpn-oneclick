"""流量采样器与聚合。"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.traffic import TrafficSample
from app.services import management

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL = max(15, int(os.environ.get("TRAFFIC_SAMPLE_SECONDS", "60") or 60))
RETENTION_DAYS = max(1, int(os.environ.get("TRAFFIC_RETENTION_DAYS", "30") or 30))

_stop = threading.Event()
_thread: threading.Thread | None = None


def sample_once(db) -> int:
    """采一次样；返回写入的行数（0 表示没有在线客户端或管理接口不可用）。"""
    try:
        online = management.list_online(force=True)
    except Exception:  # noqa: BLE001 - 采不到就跳过这一轮，不该影响面板
        logger.debug("流量采样跳过：管理接口不可用", exc_info=True)
        return 0
    if not online:
        return 0
    now = datetime.utcnow()
    for item in online:
        db.add(
            TrafficSample(
                common_name=item.common_name,
                sampled_at=now,
                bytes_received=int(getattr(item, "bytes_received", 0) or 0),
                bytes_sent=int(getattr(item, "bytes_sent", 0) or 0),
            )
        )
    db.commit()
    return len(online)


def prune(db, *, days: int = RETENTION_DAYS) -> int:
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = db.query(TrafficSample).filter(TrafficSample.sampled_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def _loop() -> None:
    rounds = 0
    while not _stop.wait(SAMPLE_INTERVAL):
        rounds += 1
        try:
            with SessionLocal() as db:
                sample_once(db)
                # 每 60 轮（约一小时）清一次过期数据
                if rounds % 60 == 0:
                    prune(db)
        except Exception:  # noqa: BLE001 - 后台线程绝不能因为一次异常而退出
            logger.exception("流量采样轮次失败")


def start_background_sampler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="traffic-sampler", daemon=True)
    _thread.start()


def stop_background_sampler() -> None:
    _stop.set()


def _delta(prev: int, cur: int) -> int:
    """累计值求增量：重连后计数归零，此时 cur < prev，应视为"从 0 开始"。"""
    if cur >= prev:
        return cur - prev
    return cur


def overview(db, *, hours: int = 24, max_points: int = 120, top: int = 5) -> dict:
    """返回可直接画图的时间序列 + Top 客户端。

    每个采样时刻会写入"当时所有在线客户端"的若干行，所以同一 ``sampled_at`` 的行数
    就是当时在线客户端数。增量按 ``common_name`` 分组、按时间排序后逐个相减。
    """
    hours = max(1, min(int(hours or 24), 24 * 30))
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(TrafficSample)
        .filter(TrafficSample.sampled_at >= cutoff)
        .order_by(TrafficSample.sampled_at.asc())
        .all()
    )
    if not rows:
        return {
            "points": [],
            "top": [],
            "hours": hours,
            "sample_interval": SAMPLE_INTERVAL,
            "retention_days": RETENTION_DAYS,
            "message": "还没有采样数据：安装或重启面板后会按间隔开始记录（在线客户端才有流量）。",
        }

    # 1) 按 common_name 求增量的累计
    totals: dict[str, dict[str, int]] = {}
    last_seen: dict[str, tuple[int, int]] = {}
    for row in rows:
        prev = last_seen.get(row.common_name)
        if prev is None:
            # 窗口内第一条不能算增量（它是"从前的累计值"），当作基线
            last_seen[row.common_name] = (row.bytes_received, row.bytes_sent)
            totals.setdefault(row.common_name, {"rx": 0, "tx": 0})
            continue
        totals.setdefault(row.common_name, {"rx": 0, "tx": 0})
        totals[row.common_name]["rx"] += _delta(prev[0], row.bytes_received)
        totals[row.common_name]["tx"] += _delta(prev[1], row.bytes_sent)
        last_seen[row.common_name] = (row.bytes_received, row.bytes_sent)

    # 2) 按时间分桶（桶内取最大在线数与增量之和）
    span = (rows[-1].sampled_at - rows[0].sampled_at).total_seconds() or 1.0
    bucket = max(SAMPLE_INTERVAL, int(span / max(1, max_points)) or SAMPLE_INTERVAL)
    buckets: dict[int, dict] = {}
    prev_by_name: dict[str, tuple[int, int]] = {}
    for row in rows:
        key = int(row.sampled_at.timestamp()) // bucket * bucket
        slot = buckets.setdefault(key, {"t": key, "online": 0, "rx": 0, "tx": 0, "names": set()})
        if row.common_name not in slot["names"]:
            slot["names"].add(row.common_name)
            slot["online"] += 1
        prev = prev_by_name.get(row.common_name)
        if prev is not None:
            slot["rx"] += _delta(prev[0], row.bytes_received)
            slot["tx"] += _delta(prev[1], row.bytes_sent)
        prev_by_name[row.common_name] = (row.bytes_received, row.bytes_sent)

    points = [
        {"t": v["t"], "online": v["online"], "rx": v["rx"], "tx": v["tx"]}
        for _, v in sorted(buckets.items())
    ]
    top_clients = sorted(
        ({"common_name": name, "rx": v["rx"], "tx": v["tx"]} for name, v in totals.items()),
        key=lambda x: x["rx"] + x["tx"],
        reverse=True,
    )[: max(1, min(top, 20))]

    return {
        "points": points,
        "top": top_clients,
        "hours": hours,
        "sample_interval": SAMPLE_INTERVAL,
        "retention_days": RETENTION_DAYS,
        "total_rx": sum(v["rx"] for v in totals.values()),
        "total_tx": sum(v["tx"] for v in totals.values()),
        "samples": len(rows),
    }
