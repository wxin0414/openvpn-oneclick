"""2FA（TOTP）接口。

安全取舍：

* ``disable`` 要求提供**一个当前有效的 6 位码**，而不是只凭登录态。否则拿到一个未过期的
  令牌就能把别人的 2FA 关掉——那等于 2FA 形同虚设。
* ``setup`` 只写 secret，``totp_enabled`` 保持 False，必须用 ``enable`` 提供一个正确码
  才真正启用。这样即使 setup 后用户没扫码，也不会把自己锁在门外。
* 恢复码只在 ``enable`` 时返回一次（明文），库里只留 sha256。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.api.security import get_current_user
from app.services import totp

router = APIRouter()


class CodePayload(BaseModel):
    code: str = Field(..., min_length=6, max_length=12)


def _qr_data_uri(text: str) -> str:
    """把 otpauth:// 画成 SVG data URI（与分享二维码同一套依赖）。"""
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
        import io

        buffer = io.BytesIO()
        img.save(buffer)
        return "data:image/svg+xml;base64," + __import__("base64").b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 - 画不出来也要把 secret 给用户，手工输入仍可用
        return ""


@router.get("/status")
def twofa_status(user=Depends(get_current_user)) -> dict:
    return {"enabled": bool(getattr(user, "totp_enabled", False))}


@router.post("/setup")
def twofa_setup(db: Session = Depends(deps.get_db), user=Depends(get_current_user)) -> dict:
    if getattr(user, "totp_enabled", False):
        raise HTTPException(status_code=400, detail="2FA 已启用，请先关闭再重新设置")
    secret = totp.generate_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    db.add(user)
    db.commit()
    uri = totp.provisioning_uri(secret, getattr(user, "username", "admin"))
    return {"secret": secret, "uri": uri, "qr": _qr_data_uri(uri)}


@router.post("/enable")
def twofa_enable(payload: CodePayload, db: Session = Depends(deps.get_db), user=Depends(get_current_user)) -> dict:
    secret = getattr(user, "totp_secret", None)
    if not secret:
        raise HTTPException(status_code=400, detail="请先调用 setup 生成密钥")
    if not totp.verify(secret, payload.code):
        raise HTTPException(status_code=400, detail="验证码不正确（确认手机时间准确）")
    codes = totp.generate_recovery_codes()
    user.totp_enabled = True
    user.totp_recovery = json.dumps([totp.hash_recovery_code(c) for c in codes])
    db.add(user)
    db.commit()
    return {"enabled": True, "recovery_codes": codes}


@router.post("/disable")
def twofa_disable(payload: CodePayload, db: Session = Depends(deps.get_db), user=Depends(get_current_user)) -> dict:
    """关闭 2FA：必须提供一个当前有效码（证明持有设备）。"""
    secret = getattr(user, "totp_secret", None)
    if not getattr(user, "totp_enabled", False):
        return {"enabled": False}
    if not totp.verify(secret or "", payload.code):
        raise HTTPException(status_code=400, detail="验证码不正确，无法关闭 2FA")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_recovery = None
    db.add(user)
    db.commit()
    return {"enabled": False}
