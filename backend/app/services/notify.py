"""告警通知：把「服务异常 / 证书到期 / 磁盘告警」推到一个 Webhook。

三条硬约束：

1. **推送失败绝不能影响主流程**——所有异常都在这里吞掉并记日志，
   调用方（后台巡检线程、测试发送接口）拿到的永远是一个结果字典。
2. 只发 ``http(s)://`` 的地址：避免有人把 webhook 当成本机任意协议探测器。
3. 有冷却时间与去重键：同类事件在冷却期内只推一次，否则告警会反复刷屏。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.notify import NotifyConfig

logger = logging.getLogger(__name__)

LEVEL_ORDER = {"warning": 1, "critical": 2}
TIMEOUT_SECONDS = 5


def load(db: Session) -> NotifyConfig:
    row = db.get(NotifyConfig, 1)
    if row is None:
        row = NotifyConfig(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def save(db: Session, **fields) -> NotifyConfig:
    row = load(db)
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _post(url: str, payload: dict) -> tuple[bool, str]:
    """返回 (成功?, 说明)。绝不抛异常。"""
    if not url.startswith(("http://", "https://")):
        return False, "webhook 地址必须是 http:// 或 https://"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - 只允许 http(s)
            return 200 <= resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 网络/超时/DNS 一律吞掉
        return False, f"{type(exc).__name__}: {exc}"


def send(db: Session, *, level: str, title: str, message: str, key: str = "") -> dict:
    """按配置推送一条通知。返回 {"sent": bool, "reason": str}。"""
    try:
        cfg = load(db)
    except Exception:  # noqa: BLE001
        logger.debug("读取通知配置失败", exc_info=True)
        return {"sent": False, "reason": "读取配置失败"}
    if not cfg.enabled or not cfg.webhook_url:
        return {"sent": False, "reason": "未启用或未配置 webhook"}
    if LEVEL_ORDER.get(level, 0) < LEVEL_ORDER.get(cfg.min_level, 2):
        return {"sent": False, "reason": f"低于推送等级（{level} < {cfg.min_level}）"}

    payload = {
        "source": "ovpn-oneclick",
        "level": level,
        "title": title,
        "message": message,
        "key": key,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    ok, detail = _post(cfg.webhook_url, payload)
    if not ok:
        logger.warning("通知推送失败：%s", detail)
    return {"sent": ok, "reason": detail}


# 冷却：{去重键: 上次推送的 monotonic 时间}
_last_sent: dict[str, float] = {}


def send_alert_once(db: Session, *, level: str, key: str, title: str, message: str) -> dict:
    """同一 key 在 cooldown 内只推一次。"""
    try:
        cfg = load(db)
        cooldown = max(0, int(cfg.cooldown_seconds or 0))
    except Exception:  # noqa: BLE001
        cooldown = 0
    now = time.monotonic()
    if cooldown and now - _last_sent.get(key, 0.0) < cooldown:
        return {"sent": False, "reason": "冷却中"}
    result = send(db, level=level, title=title, message=message, key=key)
    if result.get("sent"):
        _last_sent[key] = now
    return result
