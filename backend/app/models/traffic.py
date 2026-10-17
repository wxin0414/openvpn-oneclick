"""流量采样。

管理接口的 ``bytes_received`` / ``bytes_sent`` 是**会话累计值**，客户端重连后会归零。
因此这里原样存累计值，把"算增量"留给聚合层去做——存增量在重连那一刻会算出负数，
而存累计值可以同时回答"当前速率"和"历史总量"两个问题。

采样由后台线程每分钟执行一次（间隔与保留期都可用环境变量覆盖）。数据量很小：
10 个客户端每分钟 10 行，30 天约 43 万行，SQLite 完全够。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String

from app.db.base_class import Base


class TrafficSample(Base):
    __tablename__ = "traffic_samples"

    id = Column(Integer, primary_key=True, index=True)
    # 不建外键：客户端删除后仍要保留它的历史流量（外键级联会把历史一起删掉）
    client_id = Column(Integer, nullable=True, index=True)
    common_name = Column(String(64), nullable=False, index=True)
    sampled_at = Column(DateTime, nullable=False, index=True)
    bytes_received = Column(BigInteger, nullable=False, default=0)
    bytes_sent = Column(BigInteger, nullable=False, default=0)

    __table_args__ = (Index("ix_traffic_cn_time", "common_name", "sampled_at"),)
