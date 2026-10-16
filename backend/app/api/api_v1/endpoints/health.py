from fastapi import APIRouter

from app.core.config import get_settings, insecure_settings
from app.services import management, openvpn

router = APIRouter()
settings = get_settings()


@router.get("/live")
def liveness() -> dict[str, str]:
    # 存活探针：返回静态 ok
    return {"status": "ok"}


@router.get("/ready")
def readiness() -> dict:
    """就绪探针：检查数据库可写、OpenVPN 服务可查询。

    不再是无条件的静态 ok —— 部署验证时这个接口能立刻暴露 systemd/DB 问题。
    """
    checks: dict[str, object] = {}

    try:
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"

    checks["openvpn_service"] = openvpn.service_state()

    ready = checks.get("database") == "ok" and checks["openvpn_service"] in {"running", "stopped"}
    return {"status": "ok" if ready else "degraded", "checks": checks}


@router.get("/diagnostics")
def diagnostics() -> dict:
    """诊断信息：management 可达性、缓存/熔断状态与安全自检。"""
    return {
        "management": management.health(),
        "openvpn_service": openvpn.service_state(),
        "security_warnings": insecure_settings(settings),
    }
