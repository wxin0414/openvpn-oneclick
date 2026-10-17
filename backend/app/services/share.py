"""一次性下载链接的创建与兑换。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.share_link import ShareLink

# 默认有效期：足够发给同事，又不至于长期可用
DEFAULT_TTL_MINUTES = 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(db: Session, client: Client, *, ttl_minutes: int = DEFAULT_TTL_MINUTES, created_by: str = "") -> tuple[str, ShareLink]:
    """创建一条链接，返回 (明文令牌, 记录)。

    明文只在创建时返回一次，数据库里只有 sha256。
    """
    token = secrets.token_urlsafe(24)
    record = ShareLink(
        token_hash=_hash(token),
        client_id=client.id,
        created_by=created_by,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(minutes=max(1, min(ttl_minutes, 10080))),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return token, record


def redeem(db: Session, token: str) -> tuple[Client | None, str]:
    """兑换令牌；返回 (client, 失败原因)。

    一次性语义：兑换成功立刻写 used_at。并发下用条件更新保证只有一个请求能拿到。
    """
    digest = _hash(token or "")
    record = db.query(ShareLink).filter(ShareLink.token_hash == digest).first()
    if record is None:
        return None, "链接无效"
    if record.used_at is not None:
        return None, "链接已被使用"
    if record.expires_at < datetime.utcnow():
        return None, "链接已过期"

    # 条件更新：只有 used_at 仍为空时才置位，天然避免并发重复兑换
    updated = (
        db.query(ShareLink)
        .filter(ShareLink.id == record.id, ShareLink.used_at.is_(None))
        .update({ShareLink.used_at: datetime.utcnow()}, synchronize_session=False)
    )
    db.commit()
    if not updated:
        return None, "链接已被使用"

    client = db.query(Client).filter(Client.id == record.client_id).first()
    if client is None:
        return None, "客户端已不存在"
    return client, ""
