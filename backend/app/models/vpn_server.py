from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.utils.time import now_shanghai_naive


class VPNServer(Base):
    __tablename__ = "vpnservers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=1194)
    protocol: Mapped[str] = mapped_column(String(10), default="tcp")
    status: Mapped[str] = mapped_column(String(20), default="stopped")  # running/stopped
    vpn_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_shanghai_naive)
