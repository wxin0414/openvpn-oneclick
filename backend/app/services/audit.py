"""审计日志写入的统一入口。

原实现把 `_safe_audit` 分别在 ``endpoints/clients.py`` 和 ``services/status_sync.py``
里各写了一份；后台任务又去 import 另一个模块的私有函数。这里收敛成一处。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import crud
from app.schemas.audit_log import AuditLogCreate

logger = logging.getLogger(__name__)


def safe_audit(db: Session, actor: str, action: str, target: str | None, result: str) -> None:
    """写一条审计日志；失败只记录，绝不影响主流程。"""
    try:
        crud.audit_log.create(db, AuditLogCreate(actor=actor, action=action, target=target, result=result))
    except Exception:  # noqa: BLE001 - 审计失败不应中断业务
        logger.exception("写审计日志失败 action=%s target=%s result=%s", action, target, result)


__all__ = ["safe_audit"]
