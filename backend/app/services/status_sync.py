"""在线状态同步：把写操作从读请求里移出去。

原实现每次 ``GET /clients`` 都会：

* 调 management 取在线列表；
* 把**整张** clients 表加载进内存（完全无视分页参数）；
* 逐条比较并 ``db.commit()``；
* 为每个状态变化写一条审计记录。

于是列表这类读接口变成了写事务：SQLite 默认日志模式下写会阻塞读、客户端网络
抖动会把审计表刷爆、客户端数量增长后每次翻页都要全表扫描。

现在改为由后台线程按固定间隔执行一次同步，``GET`` 请求保持只读。
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app import crud
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services import management
from app.services.audit import safe_audit

logger = logging.getLogger(__name__)
settings = get_settings()

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def sync_once(db: Session, *, online_common_names: set[str] | None = None) -> int:
    """把数据库里的 status 字段与真实在线列表对齐。返回发生变化的条数。"""
    if online_common_names is None:
        online_common_names = {c.common_name for c in management.list_online()}

    changed = 0
    for client in db.query(crud.client.model).all():
        if _apply_expiry(db, client):
            desired = "disabled"
        elif client.disabled:
            desired = "disabled"
        elif client.common_name in online_common_names:
            desired = "online"
        else:
            desired = "offline"

        if client.status == desired:
            continue

        previous = client.status
        client.status = desired
        changed += 1
        if desired == "online":
            safe_audit(db, "system", "client_login", client.common_name, "success")
        elif previous == "online":
            safe_audit(db, "system", "client_logout", client.common_name, "success")

    if changed:
        db.commit()
    return changed


def _loop(interval: int) -> None:
    logger.info("在线状态后台同步已启动，间隔 %ss", interval)
    while not _stop_event.wait(interval):
        try:
            with SessionLocal() as db:
                sync_once(db)
        except Exception:  # noqa: BLE001 - 后台线程必须存活
            logger.exception("后台在线状态同步失败")


def start_background_sync() -> threading.Thread | None:
    """幂等地启动后台同步线程（配置关闭时返回 None）。"""
    global _thread
    if not settings.background_status_sync:
        logger.info("BACKGROUND_STATUS_SYNC 关闭，跳过后台同步")
        return None
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(settings.status_sync_interval_seconds,),
            name="ovpn-status-sync",
            daemon=True,
        )
        _thread.start()
        return _thread


def stop_background_sync() -> None:
    _stop_event.set()


__all__ = ["sync_once", "start_background_sync", "stop_background_sync"]

def _apply_expiry(db, client) -> bool:
    """到期就把 disabled 落库并写 CCD 的 disable，返回该客户端现在是否应视为禁用。

    为什么放在这里：`sync_once` 本来就每 30 秒遍历一次全部客户端，借用它就能做到
    "到期后最多 30 秒内被踢下线"，不需要再加一个调度器。

    只做一次副作用（`not client.disabled` 才写），避免每轮都重写 CCD。
    """
    from datetime import datetime

    if client.disabled:
        return True
    expires_at = getattr(client, "expires_at", None)
    expired = bool(expires_at) and expires_at <= datetime.now()
    over_quota = False if expired else _quota_exceeded(db, client)
    if not (expired or over_quota):
        return False

    client.disabled = True
    db.add(client)
    db.commit()
    try:
        from app.services import ccd as ccd_service

        routes = [r.strip() for r in (client.routes or "").split(",") if r.strip()]
        ccd_service.write_ccd(client, fixed_ip=client.fixed_ip, routes=routes, disabled=True)
    except Exception:  # noqa: BLE001 - 写 CCD 失败不能打断状态同步
        pass
    try:
        from app.services import management

        management.disconnect(client.common_name)
    except Exception:  # noqa: BLE001 - 不在线时忽略
        pass
    return True

def _quota_exceeded(db, client) -> bool:
    """客户端累计流量是否已超过 ``quota_mb``。

    口径：``traffic_samples`` 存的是管理接口给的**会话累计值**，客户端断开重连后计数会
    从 0 重新开始，所以"累计用量"不能用最后一条样本，而要按时间累加**正增量**
    （计数回退时把当前值当作新一轮的起点）。这与流量图表的口径一致。

    只有设了 quota_mb 的客户端才会查库，避免给所有客户端都加一次全表扫描。
    """
    quota_mb = getattr(client, "quota_mb", None)
    if not quota_mb:
        return False
    try:
        from app.models.traffic import TrafficSample

        rows = (
            db.query(TrafficSample.bytes_received, TrafficSample.bytes_sent)
            .filter(TrafficSample.common_name == client.common_name)
            .order_by(TrafficSample.sampled_at.asc(), TrafficSample.id.asc())
            .all()
        )
    except Exception:  # noqa: BLE001 - 查不到就当作未超额，不能因此误踢客户端
        return False

    used = 0
    prev_rx = prev_tx = 0
    for rx, tx in rows:
        rx = int(rx or 0)
        tx = int(tx or 0)
        if rx >= prev_rx and tx >= prev_tx:
            used += (rx - prev_rx) + (tx - prev_tx)
        else:
            # 会话重连：计数从头开始，这一条就是新一轮的首个增量
            used += rx + tx
        prev_rx, prev_tx = rx, tx
    return used >= int(quota_mb) * 1024 * 1024
