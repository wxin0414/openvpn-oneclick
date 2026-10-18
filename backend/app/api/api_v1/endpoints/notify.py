from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api import deps
from app.api.security import get_current_user
from app.services import notify as notify_service


def ensure_superuser(user=Depends(get_current_user)):
    """仅超级管理员可读写通知配置（只读账号不能改）。

    项目里 ensure_superuser 由各 endpoint 模块各自定义（clients.py、users.py 都有），
    没有公共实现；这里按同样语义自带一个，不去改别人的模块。
    """
    if not getattr(user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return user

router = APIRouter()


class NotifyPayload(BaseModel):
    enabled: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=500)
    min_level: str | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)

    @field_validator("min_level")
    @classmethod
    def _check_level(cls, value: str | None) -> str | None:
        if value is not None and value not in ("warning", "critical"):
            raise ValueError("min_level 只能是 warning 或 critical")
        return value

    @field_validator("webhook_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("webhook 必须是 http:// 或 https:// 地址")
        return value


@router.get("/", dependencies=[Depends(ensure_superuser)])
def read_notify(db: Session = Depends(deps.get_db)) -> dict:
    cfg = notify_service.load(db)
    return {
        "enabled": cfg.enabled,
        "webhook_url": cfg.webhook_url,
        "min_level": cfg.min_level,
        "cooldown_seconds": cfg.cooldown_seconds,
    }


@router.put("/", dependencies=[Depends(ensure_superuser)])
def update_notify(payload: NotifyPayload, db: Session = Depends(deps.get_db)) -> dict:
    cfg = notify_service.save(db, **payload.model_dump(exclude_none=True))
    return {
        "enabled": cfg.enabled,
        "webhook_url": cfg.webhook_url,
        "min_level": cfg.min_level,
        "cooldown_seconds": cfg.cooldown_seconds,
    }


@router.post("/test", dependencies=[Depends(ensure_superuser)])
def test_notify(db: Session = Depends(deps.get_db)) -> dict:
    # 测试发送忽略 enabled 与冷却，只验证"地址能不能收到"
    cfg = notify_service.load(db)
    if not cfg.webhook_url:
        return {"ok": False, "reason": "还没有配置 webhook 地址"}
    result = notify_service.send(
        db, level="critical", title="ovpn-oneclick 测试通知", message="这是一条测试消息", key="test"
    )
    return {"ok": bool(result.get("sent")), "reason": result.get("reason")}
