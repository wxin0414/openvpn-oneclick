"""仪表盘汇总接口。

性能修复：原实现在一个请求里**串行**执行了 4 次 management 连接（status 3、
status 3、load-stats、state）、1 次 systemctl 子进程和 6 次数据库查询，
其中 ``status 3`` 被完整跑了两遍。

现在：

* ``status 3`` 只抓一次，同时产出在线数、全局指标与路由表；
* 只读探测走 TTL 缓存（默认 3 秒），同一页面的重复刷新被合并；
* 服务状态走 ``systemctl show`` 机器可读查询；
* 证书统计由 4 条 COUNT 合并为 1 条条件聚合；
* management 不可达时由熔断快速失败，不再 4 × 3s 串行超时（原最坏 12s，
  已超过前端 8s 超时）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.services import management, openvpn as openvpn_service
from app.utils.time import utcnow_naive

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _certificate_stats(db: Session) -> dict[str, int]:
    """一次查询算出证书总数/有效/已吊销/已过期。"""
    cert_model = crud.certificate.model
    row = (
        db.query(
            func.count().label("total"),
            func.sum(case((cert_model.status == "valid", 1), else_=0)).label("valid"),
            func.sum(case((cert_model.status == "revoked", 1), else_=0)).label("revoked"),
            func.sum(case((cert_model.not_after < utcnow_naive(), 1), else_=0)).label("expired"),
        )
        .select_from(cert_model)
        .one()
    )
    return {
        "total": int(row.total or 0),
        "valid": int(row.valid or 0),
        "revoked": int(row.revoked or 0),
        "expired": int(row.expired or 0),
    }


@router.get("/metrics")
def get_dashboard_metrics(
    db: Session = Depends(deps.get_db),
    refresh: bool = Query(False, description="跳过缓存，强制重新探测"),
) -> dict:
    """汇总仪表盘需要的实时/统计数据。"""
    total_clients = db.query(crud.client.model).count()
    certificates = _certificate_stats(db)

    # 一次 status 3 同时给出在线数、全局指标与路由表
    details = None
    try:
        details = management.status_details(force=refresh)
    except Exception:  # noqa: BLE001 - 面板不应因为 management 故障而 500
        logger.warning("读取 management 状态失败，降级展示", exc_info=True)

    if details is not None:
        online_clients = len(details.clients)
        routing_table = [
            {
                "virtual_address": r.virtual_address,
                "real_address": r.real_address,
                "last_ref": r.last_ref,
                "common_name": r.common_name,
            }
            for r in details.routing_table
        ]
        global_stats = details.global_stats
    else:
        # management 不可用：回退 status 文件，仅能拿到在线数
        try:
            online_clients = len(management.list_online(force=refresh))
        except Exception:  # noqa: BLE001
            online_clients = 0
        routing_table = []
        global_stats = {}

    openvpn_status = openvpn_service.service_state()

    load_stats = management.load_stats(force=refresh)
    load_stats_dict = (
        {
            "nclients": load_stats.nclients,
            "bytes_in": load_stats.bytes_in,
            "bytes_out": load_stats.bytes_out,
            "uptime": load_stats.uptime,
        }
        if load_stats
        else None
    )

    state_events_dict = [
        {
            "timestamp": e.timestamp,
            "state": e.state,
            "detail": e.detail,
            "virtual_address": e.virtual_address,
            "common_name": e.common_name,
            "real_address": e.real_address,
        }
        for e in management.state_history(limit=10, force=refresh)
    ]

    recent_logs = [
        {
            "actor": log.actor,
            "action": log.action,
            "target": log.target,
            "result": log.result,
            "created_at": log.created_at.isoformat(),
        }
        for log in crud.audit_log.get_recent(db, limit=10)
    ]

    return {
        "online_clients": online_clients,
        "total_clients": total_clients,
        "certificates": certificates,
        "openvpn": {
            "status": openvpn_status,
            "title": details.title if details else None,
            "time_unix": details.time_unix if details else None,
            "time_str": details.time_str if details else None,
            "global_stats": global_stats,
            "routing_table": routing_table,
            "load_stats": load_stats_dict,
            "state_events": state_events_dict,
        },
        # 目前暂无告警系统，占位返回 0
        "alerts": {"count": 0},
        "recent_activity": recent_logs,
    }
