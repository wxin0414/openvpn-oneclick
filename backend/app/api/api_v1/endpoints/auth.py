import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.core import ratelimit
from app.core.config import KNOWN_INSECURE_PASSWORDS, get_settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.schemas.audit_log import AuditLogCreate
from app.schemas.token import PasswordChange, Token

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


def _client_ip(request: Request) -> str | None:
    """尽力取得客户端 IP；反向代理下优先 X-Forwarded-For 的第一跳。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _audit(db: Session, actor: str, action: str, target: str | None, result: str) -> None:
    try:
        crud.audit_log.create(db, AuditLogCreate(actor=actor, action=action, target=target, result=result))
    except Exception:
        logger.exception("写审计日志失败 action=%s target=%s", action, target)


@router.post("/login", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(deps.get_db),
) -> Token:
    """登录接口：验证用户名密码，签发访问 Token。

    新增：进程内限速，抵御在线暴力破解（默认 5 分钟内 8 次失败锁定 5 分钟）。
    """
    client_ip = _client_ip(request)
    wait = ratelimit.check(form_data.username, client_ip)
    if wait > 0:
        _audit(db, form_data.username, "user_login", form_data.username, "throttled")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {int(wait) + 1} 秒后重试",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    user = crud.user.get_by_username(db, username=form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        ratelimit.register_failure(form_data.username, client_ip)
        _audit(db, form_data.username, "user_login", form_data.username, "fail")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

    if not user.is_active:
        _audit(db, form_data.username, "user_login", form_data.username, "fail")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # 两步验证：开启后的用户必须再提供一个 6 位码（或一次性恢复码）。
    # 码从 query 参数 otp 或请求头 X-OTP 传，避免改动 OAuth2PasswordRequestForm 的形状
    # （前端表单编码登录已经固定，加字段会牵连整个登录调用）。
    if getattr(user, "totp_enabled", False):
        otp = (request.query_params.get("otp") or request.headers.get("X-OTP") or "").strip()
        if not otp:
            _audit(db, form_data.username, "user_login", form_data.username, "2fa_required")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要两步验证码（otp）",
                headers={"X-2FA-Required": "1"},
            )
        if not _verify_otp_or_recovery(db, user, otp):
            ratelimit.register_failure(form_data.username, client_ip)
            _audit(db, form_data.username, "user_login", form_data.username, "2fa_fail")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="两步验证码不正确")

    ratelimit.register_success(form_data.username, client_ip)
    access_token = create_access_token(
        subject=user.username, expires_delta=timedelta(minutes=settings.access_token_expire_minutes)
    )
    _audit(db, user.username, "user_login", user.username, "success")

    must_change = form_data.password in KNOWN_INSECURE_PASSWORDS
    if must_change:
        logger.warning("用户 %s 正在使用公开的默认口令，请立即修改", user.username)

    return Token(access_token=access_token, must_change_password=must_change)


@router.post("/change-password", summary="修改当前用户口令")
def change_password(
    payload: PasswordChange,
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> dict[str, str]:
    """自助修改口令；需要提供当前口令。"""
    if not verify_password(payload.current_password, user.hashed_password):
        _audit(db, user.username, "change_password", user.username, "fail")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前口令不正确")
    if len(payload.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"新口令至少需要 {MIN_PASSWORD_LENGTH} 位",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新口令不能与当前口令相同")

    crud.user.update(db, db_obj=user, obj_in={"password": payload.new_password})
    _audit(db, user.username, "change_password", user.username, "success")
    return {"detail": "password changed"}


__all__ = ["router", "get_password_hash"]

def _verify_otp_or_recovery(db: Session, user, otp: str) -> bool:
    """校验 6 位 TOTP 码，或一张未使用过的一次性恢复码（用掉即删除）。"""
    import json

    from app.services import totp

    secret = getattr(user, "totp_secret", None)
    if secret and totp.verify(secret, otp):
        return True

    raw = getattr(user, "totp_recovery", None)
    if not raw:
        return False
    try:
        stored = json.loads(raw)
    except Exception:  # noqa: BLE001 - 存储损坏时按失败处理
        return False
    hashed = totp.hash_recovery_code(otp)
    if hashed in stored:
        stored.remove(hashed)
        user.totp_recovery = json.dumps(stored)
        db.add(user)
        db.commit()
        return True
    return False
