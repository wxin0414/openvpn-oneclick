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
from datetime import timedelta

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
            # valid 必须是"状态有效且未过期"：此前只判 status，导致一张已过期的证书
            # 同时计入 valid 和 expired，四个分项加起来会超过 total。
            func.sum(
                case(
                    ((cert_model.status == "valid") & (cert_model.not_after >= utcnow_naive()), 1),
                    else_=0,
                )
            ).label("valid"),
            func.sum(case((cert_model.status == "revoked", 1), else_=0)).label("revoked"),
            # expired 同样要排除已吊销：吊销的证书本来就不该再用，算「过期」会让
            # total / valid / revoked / expired 四个分项加起来超过 total（实测 5 张证书
            # 算出 6）。口径统一为：valid + revoked + expired == total。
            func.sum(
                case(
                    ((cert_model.status != "revoked") & (cert_model.not_after < utcnow_naive()), 1),
                    else_=0,
                )
            ).label("expired"),
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
        # 告警：只统计"即将到期/已过期但未吊销"的客户端证书。
        # 之前这里是写死的 0，等于把最重要的运维提醒藏了起来。
        "alerts": _alerts(db),
        "recent_activity": recent_logs,
    }

def _expiring_certificates(db: Session, days: int) -> list[dict]:
    """即将到期或已过期的证书（已吊销的不算：它本来就不该再用）。"""
    model = crud.certificate.model
    now = utcnow_naive()
    rows = (
        db.query(model)
        .filter(model.status != "revoked")
        .filter(model.not_after <= now + timedelta(days=days))
        .order_by(model.not_after.asc())
        .all()
    )
    items: list[dict] = []
    for cert in rows:
        # 用 total_seconds 再取整：timedelta.days 是向下取整，
        # 3 天前过期会算出 -4，导致界面显示「已过期 4 天」。int() 是向零截断。
        days_left = int((cert.not_after - now).total_seconds() / 86400)
        items.append(
            {
                "common_name": cert.common_name,
                "not_after": cert.not_after.isoformat() + "Z",
                "days_left": days_left,
                "expired": days_left < 0,
                "status": cert.status,
            }
        )
    return items


@router.get("/certificate-expiry")
def certificate_expiry(
    days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(deps.get_db),
) -> dict:
    """证书到期清单（默认看未来 30 天，含已过期未吊销的）。"""
    items = _expiring_certificates(db, days)
    return {
        "items": items,
        "count": len(items),
        "expired": sum(1 for i in items if i["expired"]),
        "days": days,
    }

# --------------------------------------------------------------------------- #
# 告警汇总
# --------------------------------------------------------------------------- #
# 此前 alerts 只统计"证书 30 天内到期"。服务挂了、磁盘快满、特权助手通道失效
# 这些更急的情况都不计入，用户从仪表盘上看不出来。
# 这里复用自检的检查项，并加 30 秒缓存——仪表盘会被反复打开，不能让每次请求都去
# fork 若干 systemctl。
_ALERT_TTL_SECONDS = 30
_alert_cache: dict[str, object] = {"at": 0.0, "value": None}


def _alerts(db: Session) -> dict:
    import time

    from app.services import selfcheck as selfcheck_service

    now = time.monotonic()
    if _alert_cache["value"] is not None and now - float(_alert_cache["at"]) < _ALERT_TTL_SECONDS:  # type: ignore[arg-type]
        cached = _alert_cache["value"]
        assert isinstance(cached, dict)
        return cached

    items: list[dict] = []
    try:
        report = selfcheck_service.run_all()
        for entry in report["items"]:
            if entry["status"] in ("fail", "warn"):
                items.append(
                    {
                        "level": "critical" if entry["status"] == "fail" else "warning",
                        "key": entry["key"],
                        "message": f'{entry["name"]}：{entry["detail"]}',
                        "hint": entry["hint"],
                    }
                )
    except Exception:  # noqa: BLE001 - 告警自身不能把仪表盘打挂
        pass

    for cert in _expiring_certificates(db, 30):
        items.append(
            {
                "level": "critical" if cert["expired"] else "warning",
                "key": f'cert:{cert["common_name"]}',
                "message": (
                    f'证书已过期 {abs(cert["days_left"])} 天：{cert["common_name"]}'
                    if cert["expired"]
                    else f'证书 {cert["days_left"]} 天后到期：{cert["common_name"]}'
                ),
                "hint": "吊销并重新签发该客户端",
            }
        )

    value = {
        "count": len(items),
        "critical": sum(1 for i in items if i["level"] == "critical"),
        "warning": sum(1 for i in items if i["level"] == "warning"),
        "items": items[:20],
    }
    _alert_cache["at"] = now
    _alert_cache["value"] = value
    return value
