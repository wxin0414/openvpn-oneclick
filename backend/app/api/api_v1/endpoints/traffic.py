"""流量历史接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.security import get_current_user
from app.services import traffic as traffic_service

router = APIRouter()


@router.get("/overview")
def traffic_overview(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> dict:
    """流量历史（时间序列 + Top 客户端）。"""
    return traffic_service.overview(db, hours=hours)
