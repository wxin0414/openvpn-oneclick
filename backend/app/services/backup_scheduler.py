"""定时备份调度。

设计取舍：
* 配置存 ``data/backup-schedule.json``（面板用户可写），不建表、不做迁移——
  只有三个字段，没必要为它加一次 schema 变更。
* 直接调用与「立即备份」相同的 root 助手 ``backup-now``，通过环境变量 ``KEEP``
  传保留份数，所以保留策略与手动备份是同一套实现，不会出现两套行为。
* 线程崩溃不影响面板：循环体整体包 try，失败只记日志。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BACKUP_BIN = os.environ.get("BACKUP_BIN", "/usr/local/lib/ovpnmanager/backup-now")
CONFIG_PATH = os.environ.get(
    "BACKUP_SCHEDULE_FILE", "/opt/ovpnmanager/backend/data/backup-schedule.json"
)
DEFAULTS = {"enabled": False, "interval_hours": 24, "keep": 10, "last_run": ""}

_started = False
_lock = threading.Lock()


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            data.update(json.load(handle) or {})
    except Exception:  # noqa: BLE001 - 缺文件/坏 JSON 都退回默认值
        pass
    try:
        data["interval_hours"] = max(1, min(int(data.get("interval_hours") or 24), 168))
        data["keep"] = max(1, min(int(data.get("keep") or 10), 200))
    except Exception:  # noqa: BLE001
        data["interval_hours"], data["keep"] = 24, 10
    data["enabled"] = bool(data.get("enabled"))
    return data


def save(**fields) -> dict:
    data = load()
    for key, value in fields.items():
        if value is not None and key in DEFAULTS:
            data[key] = value
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return load()


def _due(cfg: dict) -> bool:
    if not cfg.get("enabled"):
        return False
    last = (cfg.get("last_run") or "").strip()
    if not last:
        return True
    try:
        last_at = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now() - last_at >= timedelta(hours=cfg["interval_hours"])


def run_backup_once(cfg: dict | None = None) -> dict:
    """跑一次备份，返回 {ok, message}。失败不抛异常。"""
    cfg = cfg or load()

    # 保留份数写文件给助手读：socket 激活的服务拿不到调用方的环境变量。
    try:
        keep_path = os.path.join(os.path.dirname(CONFIG_PATH), "backup-keep")
        with open(keep_path, "w", encoding="utf-8") as handle:
            handle.write(str(cfg.get("keep") or 10))
    except Exception:  # noqa: BLE001 - 写不了就沿用助手默认值
        logger.debug("写入保留份数失败", exc_info=True)

    # 必须走 socket 激活的助手通道：直接 subprocess 调会被拒
    # （实测返回 "必须以 root 运行（通过 socket 激活）"）。
    from app.services import helper_client

    try:
        _rc, stdout, stderr = helper_client.run_helper("backup", BACKUP_BIN, 300)
    except helper_client.HelperUnavailable as exc:
        logger.warning("定时备份通道不可用：%s", exc)
        return {"ok": False, "message": str(exc)}

    if "RESULT=ok" not in (stdout or ""):
        reason = (stderr or stdout or "").strip()[-200:]
        logger.warning("定时备份失败：%s", reason)
        return {"ok": False, "message": reason}
    save(last_run=datetime.now().isoformat(timespec="seconds"))
    return {"ok": True, "message": "已备份"}


def _loop() -> None:
    logger.info("定时备份线程启动")
    while True:
        time.sleep(60)
        try:
            cfg = load()
            if _due(cfg):
                logger.info("按计划执行备份（间隔 %s 小时，保留 %s 份）", cfg["interval_hours"], cfg["keep"])
                run_backup_once(cfg)
        except Exception:  # noqa: BLE001 - 线程不允许退出
            logger.warning("定时备份循环异常", exc_info=True)


def start_background_scheduler() -> bool:
    global _started
    if os.environ.get("BACKUP_SCHEDULER_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    with _lock:
        if _started:
            return False
        threading.Thread(target=_loop, name="backup-scheduler", daemon=True).start()
        _started = True
        return True
