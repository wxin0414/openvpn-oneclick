"""通知配置（Webhook；SMTP 后续补）。

存数据库而不是 /etc：配置文件在 /etc/ovpn-oneclick 下由 root 持有，面板用户写不了，
要走特权助手才行；而通知配置不涉及系统状态，放库里更简单，也不需要提权。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.utils.time import now_shanghai_naive


class NotifyConfig(Base):
    __tablename__ = "notify_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str] = mapped_column(String(500), default="")
    # warning / critical：只有达到该等级才推送，避免每次都刷
    min_level: Mapped[str] = mapped_column(String(20), default="critical")
    # 同类事件的最小间隔（秒），防抖：同一批告警不会每 30 秒重复推一次
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_shanghai_naive)
