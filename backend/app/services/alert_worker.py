"""告警巡检线程：把自检 + 证书到期算出来的告警接到通知上。

之前的问题：`/api/dashboard/metrics` 能算出 alerts，但**只有人打开面板时才计算**，
而且算出来也不会推送。没有这个线程，Webhook 永远收不到东西。

设计要点：

1. **只推状态变化**：靠 `notify.send_alert_once` 的去重键 + 冷却时间，同一条告警
   在冷却期内只发一次；正常的告警恢复不发信（避免噪音）。
2. **失败不影响主流程**：整个循环体包在 try 里，异常只记日志；线程本身也不会退出。
3. 单进程面板（systemd 固定 --workers 1），所以一个进程内线程足够，不需要分布式锁。
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def _interval_seconds() -> int:
    try:
        value = int(os.environ.get("ALERT_CHECK_SECONDS", "300"))
    except ValueError:
        return 300
    return max(30, min(value, 86400))


def _one_pass() -> None:
    from app.api.api_v1.endpoints import dashboard as dashboard_endpoints
    from app.db.session import SessionLocal
    from app.services import notify as notify_service

    db = SessionLocal()
    try:
        payload = dashboard_endpoints._alerts(db)  # noqa: SLF001 - 复用同一套告警口径
        for item in payload.get("items") or []:
            notify_service.send_alert_once(
                db,
                level=item.get("level", "warning"),
                key=item.get("key", "unknown"),
                title=f'[{item.get("level")}] {item.get("key")}',
                message=item.get("message", ""),
            )
    finally:
        db.close()


def _loop() -> None:
    interval = _interval_seconds()
    logger.info("告警巡检线程启动，间隔 %s 秒", interval)
    # 启动后先等一个间隔再跑，避免和安装完成后的自检抢资源
    while True:
        time.sleep(interval)
        try:
            _one_pass()
        except Exception:  # noqa: BLE001 - 巡检线程绝不允许因单次失败退出
            logger.warning("告警巡检失败（不影响主流程）", exc_info=True)


def start_background_alert_worker() -> bool:
    global _started
    if os.environ.get("ALERT_WORKER_ENABLED", "true").lower() in ("0", "false", "no"):
        logger.info("告警巡检线程已禁用（ALERT_WORKER_ENABLED）")
        return False
    with _lock:
        if _started:
            return False
        thread = threading.Thread(target=_loop, name="alert-worker", daemon=True)
        thread.start()
        _started = True
        return True
