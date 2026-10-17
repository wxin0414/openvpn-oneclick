"""一次性下载链接与二维码。

两个路由分开挂载：

* ``router``        —— 挂在 ``/api`` 下，**需要登录**，用于"生成链接 + 二维码"
* ``public_router`` —— 挂在 ``/share`` 下，**不需要登录**，用于"对方打开链接下载"

后者必须公开：同事拿到链接时还没连上 VPN，任何要求先连 VPN 的设计都是鸡生蛋。
安全性由令牌本身保证：一次性、限时、数据库只存 sha256，不依赖来源 IP。

为什么不把配置塞进二维码：内联证书/密钥的 ``.ovpn`` 约 8 KB，而二维码字节模式的
容量上限只有 2-3 KB。所以二维码编码的是**下载链接**（几十字符），不是配置文件。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api import deps
from app.api.security import get_current_user
from app.models.client import Client
from app.services import certs
from app.services import share as share_service
from app.services.audit import safe_audit
from app.services.client_tasks import server_endpoint

router = APIRouter()
public_router = APIRouter()


def _qr_png_data_uri(text: str) -> str:
    """把文本编成二维码 PNG，返回 data URI。

    用 PNG 而不是 SVG：部分手机浏览器对 ``data:image/svg+xml`` 支持不一致，而
    PNG 到处都能显示。这里用 qrcode 的纯 Python PNG 工厂，不需要 Pillow。
    """
    import qrcode
    import qrcode.image.svg

    # 用 SVG 而不是 PNG：PNG 走 qrcode.image.pure，需要额外依赖 pypng，
    # 缺了会静默失败（实测二维码字段直接为空）。SVG 是纯 Python 生成，无额外依赖。
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _base_url(request: Request) -> str:
    """构造对外可访问的基地址。

    优先用 nginx 传来的 ``X-Forwarded-*``，否则退回请求自身的 scheme/host。
    令牌才是凭据，这里只负责把链接拼成"对方点得开"的样子。
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""

    # nginx 传的 Host 是 $host，**不含端口**；直接用会得到 http://ip/share/xxx，
    # 对方点开必然连不上（实测 000）。这里从 panel.state 取真实监听端口补上。
    port = _panel_listen_port()
    if host and port:
        has_port = ("]:" in host) if host.startswith("[") else (":" in host)
        if not has_port:
            host = f"{host}:{port}"
    return f"{proto}://{host}".rstrip("/")


def _panel_listen_port() -> str:
    """从 /etc/ovpn-oneclick/panel.state 读面板对外端口（读不到就返回空）。"""
    try:
        text = Path("/etc/ovpn-oneclick/panel.state").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("PANEL_LISTEN_PORT="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@router.post("/clients/{client_id}/share")
def create_share_link(
    client_id: int,
    request: Request,
    ttl_minutes: int = share_service.DEFAULT_TTL_MINUTES,
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> dict:
    """为某个客户端生成一次性下载链接（含二维码）。"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="客户端不存在")

    token, record = share_service.create(
        db, client, ttl_minutes=ttl_minutes, created_by=getattr(user, "username", "?")
    )
    url = f"{_base_url(request)}/share/{token}"
    try:
        qr = _qr_png_data_uri(url)
    except Exception:  # noqa: BLE001 - 二维码生成失败不该让链接本身失败
        qr = ""
    safe_audit(db, getattr(user, "username", "?"), "client.share", client.common_name, "ok")
    return {
        "url": url,
        "token": token,
        "expires_at": record.expires_at.isoformat() + "Z",
        "qr": qr,
        "ttl_minutes": ttl_minutes,
        "note": "链接只能使用一次，过期或用过即失效；二维码内容就是这条链接。",
    }


@public_router.get("/{token}")
def download_shared(
    token: str,
    db: Session = Depends(deps.get_db),
) -> Response:
    """兑换链接并下载 .ovpn（一次性、无需登录）。"""
    client, reason = share_service.redeem(db, token)
    if client is None:
        # 410 Gone：链接曾存在但已不可用（用过/过期），比 404 更能说明情况
        raise HTTPException(status_code=410, detail=reason)

    host, port = server_endpoint(db)
    try:
        text = certs.export_ovpn(client, remote_host=host, remote_port=port)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"导出配置失败：{exc}") from exc

    filename = f"{client.common_name}.ovpn"
    return Response(
        content=text,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
