"""一次性下载链接。

为什么需要它
------------
给同事发 `.ovpn` 的现实路径是"下载文件 -> 微信/邮件传过去"，中间环节很多。改成
生成一条带一次性令牌的链接后，对方点开即可下载；再配上二维码，手机扫码就能直接
导入 OpenVPN 客户端。

为什么不把配置本身塞进二维码
----------------------------
内联证书/密钥的 `.ovpn` 约 8 KB，而二维码字节模式的上限只有 2-3 KB（还要求很大的
版本、扫起来很吃力）。所以二维码编码的是**下载链接**（几十字符），不是配置文件。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class ShareLink(Base):
    __tablename__ = "share_links"

    id = Column(Integer, primary_key=True, index=True)
    # 只存令牌的 sha256：数据库泄露时攻击者拿不到可用的下载链接
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(String(100), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    # 一次性：下载成功即写入时间，之后该链接失效
    used_at = Column(DateTime, nullable=True)

    client = relationship("Client", lazy="joined")
