"""面板自身网络与安全设置：监听端口 / 绑定域名 / 放行来源 / TLS / 管理员账号 / 会话有效期。

为什么需要特权助手
------------------
面板以非 root 运行，nginx 站点配置、TLS 私钥归 root。所以这里沿用与
``settings.py``（server.conf）完全相同的分工：

1. 面板把"想改成的样子"写成候选 ``<panel_data>/panel.candidate``（0600）；
2. 执行 ``sudo -n /usr/local/lib/ovpnmanager/apply-panel-config``（**不带任何参数**，
   路径来自 sudoers 模板，不接受调用方影响）；
3. root 助手校验、备份、重新渲染 nginx、``nginx -t``、失败回滚、reload，
   并把结果以 ``RESULT=`` / ``NEW_URL=`` / ``PORT_CHANGED=`` / ``BACKUP=`` /
   ``ROLLED_BACK=`` / ``RELOADED=`` / ``REASON=`` 输出；
4. 面板解析输出回报前端。面板自己**永远不写** nginx 配置、不碰私钥。

改端口或开 TLS 会让**当前页面立刻断开**——这是预期行为，不是失败：
响应里带 ``new_url``，前端据此提示"请访问新地址"。

路由挂在顶层 ``/panel`` 而不是 ``/settings/panel``：``settings`` 路由里已有
``/server``、``/reexport-all`` 这类路径，把新路由塞进同一前缀有被通配路由遮蔽的风险。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.api_v1.endpoints.users import ensure_superuser
from app.services.audit import safe_audit

logger = logging.getLogger(__name__)

router = APIRouter()

# --- 路径与外部依赖（都可用环境变量覆盖，便于测试） -------------------------------
PANEL_STATE = Path(os.environ.get("PANEL_STATE_FILE", "/etc/ovpn-oneclick/panel.state"))
PANEL_ENV = Path(os.environ.get("PANEL_ENV_FILE", "/opt/ovpnmanager/backend/.env"))
APPLY_PANEL_CONFIG_BIN = os.environ.get(
    "APPLY_PANEL_CONFIG_BIN", "/usr/local/lib/ovpnmanager/apply-panel-config"
)
APPLY_TIMEOUT_SECONDS = int(os.environ.get("APPLY_PANEL_TIMEOUT_SECONDS", "120"))
BACKUP_BIN = os.environ.get("BACKUP_BIN", "/usr/local/lib/ovpnmanager/backup-now")
BACKUP_TIMEOUT_SECONDS = int(os.environ.get("BACKUP_TIMEOUT_SECONDS", "180"))
PANEL_DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR", "/opt/ovpnmanager/backend/data"))
CANDIDATE = PANEL_DATA_DIR / "panel.candidate"
WEB_NAME = "ovpnmanager"

ALLOW_MODES = {"public", "vpn", "custom"}
TLS_MODES = {"off", "selfsigned", "existing"}
DOMAIN_RE = re.compile(r"^(?:_|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$")
MAX_CIDRS = 32


# --- 读当前生效值 ---------------------------------------------------------------
def _read_state() -> dict[str, str]:
    """解析 root 助手写下的 panel.state（KEY="VALUE" 形式）。文件不存在时返回空。"""
    state: dict[str, str] = {}
    try:
        text = PANEL_STATE.read_text(encoding="utf-8")
    except OSError:
        return state
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        state[key.strip()] = value.strip().strip('"').strip("'")
    return state


def _read_env_value(key: str) -> str | None:
    try:
        for raw in PANEL_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _write_env_value(key: str, value: str) -> None:
    """只改一行，其余原样保留（.env 属面板用户，面板有权写）。"""
    try:
        original = PANEL_ENV.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读不到面板环境文件：{exc}") from exc
    lines = original.splitlines()
    replaced = False
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            lines[idx] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    tmp = PANEL_ENV.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(PANEL_ENV)


def _split_cidrs(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _current_url(request: Request, state: dict[str, str]) -> str:
    """按请求的 Host 拼当前地址：面板可能绑域名，也可能是裸 IP。"""
    port = state.get("PANEL_LISTEN_PORT") or ""
    scheme = "https" if state.get("PANEL_TLS_MODE") in {"selfsigned", "existing"} else "http"
    host = (request.headers.get("host") or "").strip()
    if host:
        hostname = host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
    else:
        hostname = request.url.hostname or "127.0.0.1"
    suffix = f":{port}" if port else ""
    return f"{scheme}://{hostname}{suffix}/{WEB_NAME}/"


# --- 与助手交互 -----------------------------------------------------------------
def _write_candidate(values: dict[str, str]) -> None:
    PANEL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CANDIDATE.with_suffix(".tmp")
    body = "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CANDIDATE)


def _parse_helper(stdout: str) -> dict[str, str]:
    wanted = {"RESULT", "NEW_URL", "PORT_CHANGED", "TLS", "BACKUP",
              "ROLLED_BACK", "RELOADED", "REASON", "SIZE", "FILES"}
    parsed: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in wanted:
            parsed[key] = value.strip()
    return parsed


def _run_helper() -> tuple[int, str, str]:
    # 经 helper_client 调用：**socket 激活优先、sudo 兜底**。
    # 必须这样做的原因见 app/services/helper_client.py：受 NoNewPrivileges 限制的
    # 环境里面板内任何 sudo 都会失败，只有 systemd 以 root 拉起的通道可用。
    from app.services import helper_client

    try:
        return helper_client.run_helper(
            "panel-config", APPLY_PANEL_CONFIG_BIN, APPLY_TIMEOUT_SECONDS
        )
    except helper_client.HelperUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --- 请求模型 -------------------------------------------------------------------
class PanelSettingsIn(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    server_name: str | None = None
    allow_mode: str | None = None
    allow_cidrs: list[str] | None = None
    tls_mode: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None

    @field_validator("server_name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return "_"
        if len(value) > 253 or not DOMAIN_RE.match(value):
            raise ValueError("绑定域名只能是域名、IP 或 _")
        return value

    @field_validator("allow_mode")
    @classmethod
    def _check_allow_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ALLOW_MODES:
            raise ValueError(f"放行模式只能是 {'/'.join(sorted(ALLOW_MODES))}")
        return value

    @field_validator("tls_mode")
    @classmethod
    def _check_tls_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in TLS_MODES:
            raise ValueError(f"TLS 模式只能是 {'/'.join(sorted(TLS_MODES))}")
        return value

    @field_validator("allow_cidrs")
    @classmethod
    def _check_cidrs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for raw in value:
            item = raw.strip()
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"无效的 IP 或网段：{item}") from exc
            cleaned.append(item)
        if len(cleaned) > MAX_CIDRS:
            raise ValueError(f"放行来源最多 {MAX_CIDRS} 条")
        return cleaned


class AdminCredentialsIn(BaseModel):
    username: str | None = None
    password: str | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", value):
            raise ValueError("用户名 3-32 位，只能包含字母、数字、下划线、点、连字符")
        return value

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) < 12:
            raise ValueError("口令至少 12 位")
        if len(value) > 256:
            raise ValueError("口令过长")
        return value


class SessionIn(BaseModel):
    session_minutes: int = Field(ge=10, le=10080)


# --- 接口 -----------------------------------------------------------------------
@router.get("")
@router.get("/")
def read_panel_settings(request: Request, _=Depends(ensure_superuser)):
    state = _read_state()
    allow_mode = state.get("PANEL_ALLOW_MODE") or "vpn"
    tls_mode = state.get("PANEL_TLS_MODE") or "off"
    return {
        "port": int(state.get("PANEL_LISTEN_PORT") or 5698),
        "server_name": state.get("PANEL_SERVER_NAME") or "_",
        "allow_mode": allow_mode,
        "allow_cidrs": _split_cidrs(state.get("PANEL_ALLOW_CIDRS", "")),
        "tls_mode": tls_mode,
        "tls_cert": state.get("PANEL_TLS_CERT", ""),
        "tls_key": state.get("PANEL_TLS_KEY", ""),
        "session_minutes": int(_read_env_value("ACCESS_TOKEN_EXPIRE_MINUTES") or 120),
        "current_url": _current_url(request, state),
        "applied_at": state.get("PANEL_APPLIED_AT", ""),
        "last_backup": state.get("PANEL_LAST_BACKUP", ""),
        "state_file_present": bool(state),
    }


@router.patch("")
@router.patch("/")
def update_panel_settings(
    payload: PanelSettingsIn,
    request: Request,
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    state = _read_state()
    # 以当前生效值为基线，只覆盖本次提交的字段（助手要求七个键齐全）
    merged = {
        "LISTEN_PORT": str(payload.port if payload.port is not None
                           else int(state.get("PANEL_LISTEN_PORT") or 5698)),
        "SERVER_NAME": payload.server_name if payload.server_name is not None
        else (state.get("PANEL_SERVER_NAME") or "_"),
        "ALLOW_MODE": payload.allow_mode if payload.allow_mode is not None
        else (state.get("PANEL_ALLOW_MODE") or "vpn"),
        "ALLOW_CIDRS": "",
        "TLS_MODE": payload.tls_mode if payload.tls_mode is not None
        else (state.get("PANEL_TLS_MODE") or "off"),
        "TLS_CERT": payload.tls_cert if payload.tls_cert is not None
        else state.get("PANEL_TLS_CERT", ""),
        "TLS_KEY": payload.tls_key if payload.tls_key is not None
        else state.get("PANEL_TLS_KEY", ""),
    }
    if payload.allow_cidrs is not None:
        merged["ALLOW_CIDRS"] = ",".join(payload.allow_cidrs)
    elif merged["ALLOW_MODE"] == "custom":
        merged["ALLOW_CIDRS"] = state.get("PANEL_ALLOW_CIDRS", "")

    if merged["ALLOW_MODE"] == "custom" and not merged["ALLOW_CIDRS"]:
        raise HTTPException(status_code=400, detail="自定义放行模式必须至少给一条 IP 或网段")
    if merged["TLS_MODE"] == "existing":
        if not merged["TLS_CERT"] or not merged["TLS_KEY"]:
            raise HTTPException(status_code=400, detail="使用已有证书时必须同时提供证书与私钥路径")

    _write_candidate(merged)
    code, stdout, stderr = _run_helper()
    parsed = _parse_helper(stdout)
    result = parsed.get("RESULT", "")

    if result != "ok":
        reason = parsed.get("REASON") or (stderr.strip()[:300] if stderr else "助手未返回成功结果")
        safe_audit(db, getattr(user, "username", "?"), "panel.settings", merged.get("LISTEN_PORT"), f"failed: {reason}")
        # 助手失败时**不落库**：panel.state 由助手自己维护，失败不会更新它
        raise HTTPException(status_code=500, detail={"message": "面板设置未生效", "reason": reason,
                                                     "raw": stdout.strip()[-500:]})

    safe_audit(db, getattr(user, "username", "?"), "panel.settings", merged.get("LISTEN_PORT"),
               f"ok port_changed={parsed.get('PORT_CHANGED')}")
    new_state = _read_state()
    return {
        "ok": True,
        "new_url": parsed.get("NEW_URL") or _current_url(request, new_state),
        "port_changed": parsed.get("PORT_CHANGED") == "yes",
        "tls": parsed.get("TLS", merged["TLS_MODE"]),
        "backup": parsed.get("BACKUP", ""),
        "reloaded": parsed.get("RELOADED") == "yes",
        "port": int(new_state.get("PANEL_LISTEN_PORT") or merged["LISTEN_PORT"]),
        "server_name": new_state.get("PANEL_SERVER_NAME") or merged["SERVER_NAME"],
        "allow_mode": new_state.get("PANEL_ALLOW_MODE") or merged["ALLOW_MODE"],
        "allow_cidrs": _split_cidrs(new_state.get("PANEL_ALLOW_CIDRS", "")),
        "tls_mode": new_state.get("PANEL_TLS_MODE") or merged["TLS_MODE"],
    }


@router.post("/admin-credentials")
def update_admin_credentials(
    payload: AdminCredentialsIn,
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    """改管理员用户名/口令。只动数据库，不需要 root，因此即时生效。"""
    if not payload.username and not payload.password:
        raise HTTPException(status_code=400, detail="用户名与口令至少要改一项")

    if payload.username and payload.username != user.username:
        if crud.user.get_by_username(db, username=payload.username):
            raise HTTPException(status_code=400, detail="该用户名已存在")
        user = crud.user.update(db, db_obj=user, obj_in={"username": payload.username})

    if payload.password:
        user = crud.user.update(db, db_obj=user, obj_in={"password": payload.password})

    safe_audit(db, getattr(user, "username", "?"), "panel.admin_credentials",
               getattr(user, "username", None),
               "username+password" if (payload.username and payload.password) else
               ("username" if payload.username else "password"))
    return {"ok": True, "username": user.username,
            "note": "当前会话仍然有效；若改了口令，其他已登录会话会在令牌到期后失效。"}


@router.get("/session")
def read_session(_=Depends(ensure_superuser)):
    return {"session_minutes": int(_read_env_value("ACCESS_TOKEN_EXPIRE_MINUTES") or 120)}


@router.post("/session")
def update_session(
    payload: SessionIn,
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    """改会话有效期。写 .env 后需要重启面板才生效（接口会如实告知）。"""
    _write_env_value("ACCESS_TOKEN_EXPIRE_MINUTES", str(payload.session_minutes))
    safe_audit(db, getattr(user, "username", "?"), "panel.session",
               str(payload.session_minutes), "ok")
    return {
        "ok": True,
        "session_minutes": payload.session_minutes,
        "restart_required": True,
        "note": "已写入 .env，需要重启面板后生效；重启会短暂中断当前连接。",
    }


@router.post("/backup")
def create_backup(db: Session = Depends(deps.get_db), user=Depends(ensure_superuser)):
    """一键备份：PKI + server.conf + .env + app.db -> /var/backups/ovpn-oneclick。

    只读操作：只产出 tar.gz（0600），不改任何配置、不重启任何服务，因此没有让
    用户二次确认的必要。备份含 CA 私钥与 SECRET_KEY，所以文件权限是 0600。
    """
    from app.services import helper_client

    try:
        _rc, stdout, stderr = helper_client.run_helper(
            "backup", BACKUP_BIN, BACKUP_TIMEOUT_SECONDS
        )
    except helper_client.HelperUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    parsed = _parse_helper(stdout)
    if parsed.get("RESULT") != "ok":
        reason = parsed.get("REASON") or (stderr or "").strip()[:300] or "助手未返回成功结果"
        safe_audit(db, getattr(user, "username", "?"), "panel.backup", None, f"failed: {reason}")
        raise HTTPException(status_code=500, detail=f"备份失败：{reason}")

    path = parsed.get("BACKUP", "")
    safe_audit(db, getattr(user, "username", "?"), "panel.backup", path, "ok")
    return {
        "ok": True,
        "backup": path,
        "size": int(parsed.get("SIZE") or 0),
        "files": int(parsed.get("FILES") or 0),
        "note": "备份含 CA 私钥与 SECRET_KEY，文件权限 0600，请勿外传。",
    }


@router.post("/restart")
def restart_panel(db: Session = Depends(deps.get_db), user=Depends(ensure_superuser)):
    """重启面板服务（走已有的 systemctl 包装脚本，sudoers 已授权 restart）。"""
    bin_path = os.environ.get("SYSTEMCTL_BIN", "/usr/local/lib/ovpnmanager/systemctl-wrapper")
    try:
        proc = subprocess.run([bin_path, "restart"], capture_output=True, text=True,
                              timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"重启面板失败：{exc}") from exc
    ok = proc.returncode == 0
    safe_audit(db, getattr(user, "username", "?"), "panel.restart", "ovpnmanager",
               "ok" if ok else f"failed: {proc.stderr.strip()[:200]}")
    if not ok:
        raise HTTPException(status_code=500,
                            detail={"message": "重启面板失败",
                                    "stderr": proc.stderr.strip()[:300]})
    return {"ok": True, "note": "面板已重启，请稍候刷新页面。"}
