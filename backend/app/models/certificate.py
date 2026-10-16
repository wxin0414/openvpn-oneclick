from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    serial_number: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    common_name: Mapped[str] = mapped_column(String(100), index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime)
    not_after: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="valid")  # valid/revoked/expired
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    client = relationship("Client", back_populates="certificate", uselist=False)
    vpn_server_id: Mapped[int | None] = mapped_column(ForeignKey("vpnservers.id"), nullable=True)
