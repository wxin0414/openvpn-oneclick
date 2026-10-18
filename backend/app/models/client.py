from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.utils.time import now_shanghai_naive


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    common_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="offline")  # online/offline/disabled
    fixed_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    routes: Mapped[str | None] = mapped_column(String(500), nullable=True)  # comma-separated routes
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 到期时间：到点后由 reconcile 写入 CCD 的 disable，客户端立即连不上
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 流量配额（MB，累计）。达到后同样写 disable；NULL 表示不限
    quota_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_shanghai_naive)

    certificate_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    certificate = relationship("Certificate", back_populates="client", uselist=False)
