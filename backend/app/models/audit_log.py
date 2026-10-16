from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.utils.time import now_shanghai_naive


class AuditLog(Base):
    # 注意：这里沿用 SQLAlchemy 由类名推导出的旧表名 "auditlog"（原模型没有显式
    # 声明 __tablename__）。若改成 "audit_logs"，create_all 会新建一张空表，
    # 已有部署的审计历史会被孤立、面板上看不到旧记录。
    __tablename__ = "auditlog"
    # 审计日志页按 created_at 倒序分页、仪表盘取最近 N 条，
    # 过去 created_at 没有索引，查询会全表扫描并排序，随日志增长持续变慢。
    __table_args__ = (
        Index("ix_auditlog_created_at", "created_at"),
        Index("ix_auditlog_action_created_at", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(200))
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[str] = mapped_column(String(50), default="success")  # success/fail
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_shanghai_naive)
