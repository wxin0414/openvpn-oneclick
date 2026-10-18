from datetime import timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.core.config import get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
settings = get_settings()


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(deps.get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = crud.user.get_by_username(db, username=username)
    if user is None:
        raise credentials_exception
    # 修复：原实现只在登录时检查 is_active。管理员停用某个账号后，该账号手里
    # 未过期的 Token 仍然可以调用所有接口（最长 ACCESS_TOKEN_EXPIRE_MINUTES
    # 分钟）。停用必须立刻生效。
    # 改口令后旧令牌必须立刻失效：令牌里的 iat 早于 password_changed_at 就拒绝。
    # 没有这一步时，口令疑似泄露后改口令，泄露的令牌仍可用满 ACCESS_TOKEN_EXPIRE_MINUTES。
    changed_at = getattr(user, "password_changed_at", None)
    if changed_at is not None:
        issued_at = payload.get("iat")
        if issued_at is None:
            raise credentials_exception
        try:
            issued_ts = int(issued_at)
        except (TypeError, ValueError):
            raise credentials_exception
        if issued_ts < int(changed_at.replace(tzinfo=timezone.utc).timestamp()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Password changed, please sign in again",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return user
