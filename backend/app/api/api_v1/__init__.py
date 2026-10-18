from fastapi import APIRouter

from app.api.api_v1.endpoints import (
    share,
    traffic,
    auth,
    clients,
    dashboard,
    health,
    jobs,
    logs,
    notify,
    twofa,
    openvpn,
    panel,
    servers,
    settings,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(openvpn.router, prefix="/openvpn", tags=["openvpn"])
api_router.include_router(servers.router, prefix="/servers", tags=["servers"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
# 服务端设置：只暴露"可编辑项"，实际落地由 root 助手完成（见 endpoints/settings.py）
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
# 面板自身的网络与安全设置：挂在顶层 /panel，避免被 /settings 下的通配路由遮蔽
api_router.include_router(panel.router, prefix="/panel", tags=["panel"])
api_router.include_router(share.router, tags=["share"])
api_router.include_router(traffic.router, prefix="/traffic", tags=["traffic"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(notify.router, prefix="/notify", tags=["notify"])
api_router.include_router(twofa.router, prefix="/2fa", tags=["2fa"])
