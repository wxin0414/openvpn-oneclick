"""只读运维角色（viewer）的写操作拦截。

为什么用全局中间件而不是给每个端点加依赖：写操作分散在十几个路由文件里，逐个加
依赖一定会漏（尤其以后新增的端点）。中间件只认两件事——HTTP 方法是不是写、路径
是不是 /api/——漏不掉；新增路由自动受保护。

代价是每个写请求多一次 JWT 解码与一次用户查询（只读请求完全不受影响）。
"""

from __future__ import annotations

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app import crud
from app.core.config import get_settings
from app.db.session import SessionLocal

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 登录/刷新本身是 POST，必须放行，否则只读账号连登录都做不到
EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/refresh", "/api/auth/token"})

FORBIDDEN_DETAIL = "只读账号（viewer）不能执行修改操作；请改用管理员账号。"


class ReadOnlyGuard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # noqa: ANN001, ANN201
        if request.method not in WRITE_METHODS:
            return await call_next(request)
        path = request.url.path.rstrip("/") or "/"
        if not path.startswith("/api/") or path in EXEMPT_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            # 没带令牌：交给端点自己回 401，这里不越权判断
            return await call_next(request)

        token = header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        except JWTError:
            # 令牌无效同样交给端点回 401，避免把 401 伪装成 403
            return await call_next(request)

        username = payload.get("sub")
        role = "admin"
        try:
            with SessionLocal() as db:
                user = crud.user.get_by_username(db, username=username) if username else None
                if user is not None:
                    role = getattr(user, "role", "admin") or "admin"
        except Exception:  # noqa: BLE001 - 数据库异常不该把写请求全拦死
            return await call_next(request)

        if role != "admin":
            return JSONResponse(status_code=403, content={"detail": FORBIDDEN_DETAIL})
        return await call_next(request)
