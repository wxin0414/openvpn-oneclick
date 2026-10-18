"""TOTP（RFC 6238）实现，不引入第三方依赖。

设计要点：

* 只用标准库（hmac/hashlib/base64）——2FA 是登录路径上的关键能力，越少依赖越不容易
  因为某个包升级而失效。
* 允许 ±1 个时间步（30 秒）的漂移，避免客户端与服务器时钟差几秒就登不上。
* 恢复码只存 sha256，明文只在生成时返回一次；用掉就删除，不能重复使用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # 允许前后各一个时间步


def generate_secret(length: int = 20) -> str:
    """生成 base32 密钥（Google Authenticator 等客户端认这个格式）。"""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def _normalize(secret: str) -> bytes:
    padded = secret.strip().upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, counter: int) -> str:
    key = _normalize(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, *, now: float | None = None) -> bool:
    """校验 6 位验证码，允许 ±WINDOW 个时间步。"""
    if not secret or not code:
        return False
    cleaned = str(code).strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    timestamp = now if now is not None else time.time()
    counter = int(timestamp // STEP_SECONDS)
    for drift in range(-WINDOW, WINDOW + 1):
        try:
            if hmac.compare_digest(code_at(secret, counter + drift), cleaned):
                return True
        except Exception:  # noqa: BLE001 - 密钥损坏时按校验失败处理
            return False
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "ovpn-oneclick") -> str:
    """otpauth:// URI，交给客户端或二维码生成器。"""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def generate_recovery_codes(count: int = 8) -> list[str]:
    """生成人类可读的恢复码（每张只能使用一次）。"""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5)  # 10 hex chars
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()
