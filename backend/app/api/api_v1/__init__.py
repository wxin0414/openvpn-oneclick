from fastapi import APIRouter

from app.api.api_v1.endpoints import auth, clients, dashboard, health, jobs, logs, openvpn, servers, users

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(openvpn.router, prefix="/openvpn", tags=["openvpn"])
api_router.include_router(servers.router, prefix="/servers", tags=["servers"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
