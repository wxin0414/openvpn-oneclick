from fastapi import APIRouter, Depends

from app.api.security import get_current_user
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

@router.get("/selfcheck", summary="一键自检（需登录）")
def selfcheck(user=Depends(get_current_user)) -> dict:
    """把 verify.sh 的检查搬到界面。只读，不重启也不改配置。

    与 /diagnostics 的区别：那个被 nginx 限制成只能本机访问（给监控用），浏览器调不到；
    这个需要登录，可以直接从面板点。
    """
    from app.services import selfcheck as selfcheck_service

    return selfcheck_service.run_all()
