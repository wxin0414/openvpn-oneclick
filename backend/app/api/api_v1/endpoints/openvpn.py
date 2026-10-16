from fastapi import APIRouter, Depends, HTTPException

from app.api import deps
from app.api.security import get_current_user
from app.services import openvpn

router = APIRouter()


@router.get("/status")
def status(user=Depends(get_current_user)) -> dict[str, str]:
    """查询 OpenVPN systemd 服务状态（超级管理员）。

    返回机器可读的 ``state``（running/stopped/unknown），同时保留
    ``result`` 人类可读文本以兼容旧前端。
    """
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return {"action": "status", "result": openvpn.service_status_text(), "state": openvpn.service_state()}


# 注意声明顺序：这条必须放在 ``/{action}`` **之前**。
# 否则 "/import" 会先匹配上 ``/{action}``，而 action 是
# Literal["status","start","stop","restart"]，"import" 校验失败直接返回 422，
# 导入接口永远不可达（上游原实现就是这个顺序，等于该功能一直是坏的）。
# 回归保护见 tests/test_fixes.py 的 H1/H7（H7 会扫描整个应用的路由表）。
@router.post("/import", summary="Import local OpenVPN server and CCD clients")
def import_from_host(user=Depends(get_current_user), db=Depends(deps.get_db)) -> dict[str, int]:
    """从本机 OpenVPN 配置导入服务器与 CCD 客户端（超级管理员）。"""
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    from app.services.importer import import_openvpn

    try:
        stats = import_openvpn(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return stats


@router.post("/{action}", summary="Control OpenVPN service (systemctl)")
def control_service(action: openvpn.ServiceAction, user=Depends(get_current_user)) -> dict[str, str]:
    """控制 OpenVPN 服务：start/stop/restart/status（超级管理员）。"""
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    try:
        output = openvpn.control(action)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"action": action, "result": output, "state": openvpn.service_state()}
