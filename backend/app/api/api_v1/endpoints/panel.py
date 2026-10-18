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

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
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
# 索引由 backup-now 追加，0644、不含密钥内容，面板无需 root 即可读
BACKUP_INDEX = os.environ.get("BACKUP_INDEX_FILE", "/etc/ovpn-oneclick/backup-index.jsonl")
# 备份目录（root 助手写入；目录 0750、文件 0640 归面板组，下载接口才能读）
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/var/backups/ovpn-oneclick")
RESTORE_BIN = os.environ.get("RESTORE_BIN", "/usr/local/lib/ovpnmanager/restore-backup")
RESTORE_TIMEOUT_SECONDS = int(os.environ.get("RESTORE_TIMEOUT_SECONDS", "300"))
NET_BIN = os.environ.get("NET_BIN", "/usr/local/lib/ovpnmanager/net-control")
NET_TIMEOUT_SECONDS = int(os.environ.get("NET_TIMEOUT_SECONDS", "120"))
NET_STATE = os.environ.get("NET_STATE_FILE", "/etc/ovpn-oneclick/net.state")
NET_CANDIDATE = Path(os.environ.get("NET_CANDIDATE", "/opt/ovpnmanager/backend/data/net.candidate"))
# 升级检查读取的远端地址（固定，不接受调用方传入）
UPDATE_BIN = os.environ.get("UPDATE_BIN", "/usr/local/lib/ovpnmanager/self-update")
UPDATE_TIMEOUT_SECONDS = int(os.environ.get("UPDATE_TIMEOUT_SECONDS", "120"))
UPGRADE_STATE = os.environ.get("UPGRADE_STATE_FILE", "/etc/ovpn-oneclick/upgrade.state")
UPGRADE_VERSION_URL = os.environ.get(
    "UPGRADE_VERSION_URL",
    "https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/VERSION.txt",
)
PANEL_DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR", "/opt/ovpnmanager/backend/data"))
CANDIDATE = PANEL_DATA_DIR / "panel.candidate"
WEB_NAME = "ovpnmanager"

ALLOW_MODES = {"public", "vpn", "custom"}
TLS_MODES = {"off", "selfsigned", "existing"}
DOMAIN_RE = re.compile(r"^(?:_|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$")
MAX_CIDRS = 32


# --- 读当前生效值 ---------------------------------------------------------------
def _read_kv_file(path: str) -> dict[str, str]:
    """解析任意 KEY="VALUE" 形式的状态文件（不存在或不可读时返回空）。"""
    result: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


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
              "ROLLED_BACK", "RELOADED", "REASON", "SIZE", "FILES", "RESTORED", "PRE_BACKUP", "IP_FORWARD", "EGRESS_IF", "NET", "WARNING", "STATUS", "FROM", "TO", "LOG"}
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
def read_panel_settings(request: Request, _=Depends(get_current_user)):
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
        else state.get("PANEL_TLS_KEY", ""),    }

    # 关闭 TLS 时必须同时清空证书路径。助手的校验规定"mode=off 不接受自定义证书路径"，
    # 而这里默认沿用 state 里保留的旧路径，于是 {"tls_mode":"off"} 会被助手拒绝
    # ——用户从界面**关不掉** TLS（真机实测踩到，只能靠显式传空字符串绕过）。
    if merged["TLS_MODE"] == "off":
        merged["TLS_CERT"] = ""
        merged["TLS_KEY"] = ""
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
def read_session(_=Depends(get_current_user)):
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


@router.get("/backups")
def list_backups(_=Depends(get_current_user)):
    """列出最近的手动备份（读助手写的索引，不需要 root）。

    只返回路径/大小/条目数/时间，不含任何密钥内容。
    """
    import json as _json

    entries: list[dict] = []
    try:
        with open(BACKUP_INDEX, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(_json.loads(line))
                except ValueError:
                    continue  # 坏行跳过，不让一条脏数据毁掉整个列表
    except OSError:
        return {"ok": True, "items": [], "note": "还没有通过面板做过备份"}
    entries.reverse()
    return {"ok": True, "items": entries[:50]}


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
        "note": "备份含 CA 私钥与 SECRET_KEY，文件权限 0640（属组 ovpnpanel，面板可下载），请勿外传。",
    }


@router.post("/restore")
def restore_backup(
    payload: dict = Body(...),
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    """从备份恢复（危险操作）。

    三道闸门：
      1) 请求体必须带 confirm="RESTORE"（前端要求用户手打，不是点一下确定）；
      2) 只接受 /var/backups/ovpn-oneclick 下的 manual-*.tar.gz（助手内部还会再校验一次）；
      3) 助手自己会先做一次"恢复前备份"，并在失败时整体还原。

    面板会在**响应发出之后**再重启自己：恢复会覆盖 app.db，运行中的进程还握着旧库，
    必须重启才能生效。所以这里用后台定时器，3 秒后再重启，保证响应先发出去。
    """
    from app.services import helper_client

    if str(payload.get("confirm", "")).strip().upper() != "RESTORE":
        raise HTTPException(status_code=400, detail="需要确认：请在请求里带 confirm=RESTORE")

    target = str(payload.get("backup", "")).strip()
    if not target.startswith("/var/backups/ovpn-oneclick/manual-") or not target.endswith(".tar.gz"):
        raise HTTPException(
            status_code=400,
            detail="只允许恢复 /var/backups/ovpn-oneclick/manual-*.tar.gz",
        )
    if "/../" in target:
        raise HTTPException(status_code=400, detail="路径里不允许出现 ..")

    # 候选文件：0600、属面板用户；助手从这里读目标，避免"用参数传路径"
    try:
        CANDIDATE.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATE.write_text(f"BACKUP={target}\n", encoding="utf-8")
        os.chmod(CANDIDATE, 0o600)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法写候选文件：{exc}") from exc

    try:
        _rc, stdout, stderr = helper_client.run_helper(
            "restore", RESTORE_BIN, RESTORE_TIMEOUT_SECONDS
        )
    except helper_client.HelperUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    parsed = _parse_helper(stdout)
    if parsed.get("RESULT") != "ok":
        reason = parsed.get("REASON") or (stderr or "").strip()[:300] or "助手未返回成功结果"
        safe_audit(db, getattr(user, "username", "?"), "panel.restore", target, f"failed: {reason}")
        raise HTTPException(status_code=500, detail=f"恢复失败：{reason}")

    safe_audit(db, getattr(user, "username", "?"), "panel.restore", target, "ok")
    _schedule_self_restart()
    return {
        "ok": True,
        "restored": parsed.get("RESTORED", target),
        "pre_backup": parsed.get("PRE_BACKUP", ""),
        "restart_scheduled": True,
        "note": (
            "已恢复并重启 OpenVPN。面板将在约 3 秒后自动重启以加载恢复后的数据库，"
            "请稍后刷新页面重新登录。"
        ),
    }


def _schedule_self_restart(delay_seconds: float = 3.0) -> None:
    """响应发出后再重启面板自己（恢复会覆盖 app.db，运行中的进程必须重启）。"""
    import threading

    def _restart() -> None:
        try:
            # 走 socket 助手：面板带 NoNewPrivileges=1，直接调包装脚本会被 sudo 拒绝
            from app.services import openvpn as openvpn_service

            openvpn_service.control_unit("restart", "ovpnmanager")
        except Exception:  # noqa: BLE001 - 重启失败也不该留下异常线程
            logger.exception("恢复后自动重启面板失败")

    timer = threading.Timer(delay_seconds, _restart)
    timer.daemon = True
    timer.start()


@router.get("/network")
def read_network(_=Depends(get_current_user)):
    """读当前网络状态：ip_forward 直接读 /proc（只读），出口网卡读助手写的 state。"""
    forward = False
    try:
        forward = Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="utf-8").strip() == "1"
    except OSError:
        pass
    state = _read_kv_file(NET_STATE)
    # 可选出口网卡：直接列 /sys/class/net（非 root 可读），让前端给下拉而不是手输
    try:
        interfaces = sorted(name for name in os.listdir("/sys/class/net") if name != "lo")
    except OSError:
        interfaces = []
    return {
        "ip_forward": forward,
        "interfaces": interfaces,
        "egress_if": state.get("EGRESS_IF", ""),
        "net": state.get("NET", ""),
        "applied_at": state.get("APPLIED_AT", ""),
        "state_present": bool(state),
    }


@router.post("/network")
def update_network(
    payload: dict = Body(...),
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    """改 ip_forward 与 NAT 出口网卡。

    网卡必须真实存在且确有默认路由——助手会再校验一次；改错会让已连客户端立刻
    失去出网，而 OpenVPN 服务本身还是 active（重启发现不了），所以这一条不能省。
    """
    from app.services import helper_client

    want_forward = payload.get("ip_forward")
    if not isinstance(want_forward, bool):
        raise HTTPException(status_code=400, detail="ip_forward 必须是 true 或 false")

    egress = str(payload.get("egress_if") or "").strip()
    if egress and not re.match(r"^[A-Za-z0-9_.:-]{1,15}$", egress):
        raise HTTPException(status_code=400, detail="网卡名不合法")

    try:
        NET_CANDIDATE.parent.mkdir(parents=True, exist_ok=True)
        NET_CANDIDATE.write_text(
            f"IP_FORWARD={1 if want_forward else 0}\nEGRESS_IF={egress}\n", encoding="utf-8"
        )
        os.chmod(NET_CANDIDATE, 0o600)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法写候选文件：{exc}") from exc

    try:
        _rc, stdout, stderr = helper_client.run_helper("net", NET_BIN, NET_TIMEOUT_SECONDS)
    except helper_client.HelperUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    parsed = _parse_helper(stdout)
    if parsed.get("RESULT") != "ok":
        reason = parsed.get("REASON") or (stderr or "").strip()[:300] or "助手未返回成功结果"
        safe_audit(db, getattr(user, "username", "?"), "panel.network", egress or "-", f"failed: {reason}")
        raise HTTPException(status_code=500, detail=f"网络设置未生效：{reason}")

    safe_audit(db, getattr(user, "username", "?"), "panel.network", parsed.get("EGRESS_IF", egress),
               f"ok ip_forward={parsed.get('IP_FORWARD')}")
    return {
        "ok": True,
        "ip_forward": parsed.get("IP_FORWARD") == "1",
        "egress_if": parsed.get("EGRESS_IF", egress),
        "net": parsed.get("NET", ""),
        "warning": parsed.get("WARNING", ""),
        "note": "已应用。NAT 规则已重建并写入 ovpn-oneclick-nat.service（开机自恢复）。",
    }


@router.get("/upgrade/check")
def check_upgrade(_=Depends(get_current_user)):
    """检查是否有新版本。只读：拉远端 VERSION.txt 比对，不下载、不执行任何东西。

    远端地址固定在本仓库的 raw 上（不接收调用方传入的 URL，避免变成 SSRF 跳板）。
    网络不通时如实返回 error，而不是假装"已是最新"。
    """
    import urllib.error
    import urllib.request

    state = _read_kv_file("/etc/ovpn-oneclick/state.conf")
    current = state.get("INSTALLED_VERSION") or "unknown"
    latest = ""
    error = ""
    try:
        request = urllib.request.Request(  # noqa: S310 - 固定 https 地址
            UPGRADE_VERSION_URL, headers={"User-Agent": "ovpn-oneclick-panel"}
        )
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310
            latest = response.read(64).decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        error = f"无法获取最新版本：{exc}"

    def _tuple(text: str) -> tuple[int, ...]:
        parts = []
        for piece in text.split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    update_available = False
    if latest and current != "unknown":
        try:
            update_available = _tuple(latest) > _tuple(current)
        except ValueError:
            update_available = False

    return {
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "error": error,
        "note": "检查会读取本仓库的 VERSION.txt，只比对版本号；真正升级会先做备份。",
    }


@router.get("/upgrade/status")
def upgrade_status(_=Depends(get_current_user)):
    """读升级进度。升级是异步的（会重启面板），所以前端轮询这个接口。"""
    state = _read_kv_file(UPGRADE_STATE)
    if not state:
        return {"status": "idle", "from": "", "to": "", "log": "", "reason": ""}
    return {
        "status": state.get("STATUS", "unknown"),
        "from": state.get("FROM", ""),
        "to": state.get("TO", ""),
        "started_at": state.get("STARTED_AT", ""),
        "finished_at": state.get("FINISHED_AT", ""),
        "log": state.get("LOG", ""),
        "reason": state.get("REASON", ""),
    }


@router.post("/upgrade")
def start_upgrade(
    payload: dict = Body(...),
    db: Session = Depends(deps.get_db),
    user=Depends(ensure_superuser),
):
    """触发在线升级（异步）。

    这是本项目唯一会"下载并执行远端代码"的动作，所以：
      - 必须手打 confirm="UPGRADE"；
      - 下载地址写死在 root 所有的 panel.conf，面板不能传；
      - 助手对下载到的脚本做标记自检（缺 SCRIPT_VERSION / 项目标识就拒绝执行）；
      - 只调用安装器的 `--update`（它内部会先 backup_current）；
      - 立即返回 STARTED，进度由 GET /upgrade/status 轮询（升级会重启面板）。
    """
    from app.services import helper_client

    # Docker 部署：镜像里没有 self-update 助手，升级的正确做法是重建镜像。
    # 直接返回可执行的指引，而不是让按钮撞出一个 "command not found"。
    if os.environ.get("OVPN_DOCKER") == "1":
        raise HTTPException(
            status_code=400,
            detail="Docker 部署不支持在线升级：请在宿主机重新构建镜像并重启容器（docker compose up -d --build）",
        )

    if str(payload.get("confirm", "")).strip().upper() != "UPGRADE":
        raise HTTPException(status_code=400, detail="需要确认：请在请求里带 confirm=UPGRADE")

    try:
        _rc, stdout, stderr = helper_client.run_helper("self-update", UPDATE_BIN, UPDATE_TIMEOUT_SECONDS)
    except helper_client.HelperUnavailable as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    parsed = _parse_helper(stdout)
    if parsed.get("RESULT") != "ok":
        reason = parsed.get("REASON") or (stderr or "").strip()[:300] or "助手未返回成功结果"
        safe_audit(db, getattr(user, "username", "?"), "panel.upgrade", "-", f"failed: {reason}")
        raise HTTPException(status_code=500, detail=f"升级未启动：{reason}")

    safe_audit(db, getattr(user, "username", "?"), "panel.upgrade",
               f"{parsed.get('FROM', '?')}->{parsed.get('TO', '?')}", "started")
    return {
        "ok": True,
        "started": True,
        "from": parsed.get("FROM", ""),
        "to": parsed.get("TO", ""),
        "log": parsed.get("LOG", ""),
        "note": (
            "升级已在后台开始。面板会在升级过程中重启，请稍后刷新页面，"
            "并在「当前状态」里确认版本号已变化。"
        ),
    }


@router.post("/restart")
def restart_panel(db: Session = Depends(deps.get_db), user=Depends(ensure_superuser)):
    """重启面板服务（走已有的 systemctl 包装脚本，sudoers 已授权 restart）。"""
    # 必须延迟重启：同步重启会把这个请求自己的连接切断，客户端只能拿到 502
    # （实测），看起来像是"重启失败"，其实服务好好的。改成先返回、3 秒后再重启，
    # 与恢复备份后的自重启走同一条路（内部走 socket 助手，不用 sudo）。
    _schedule_self_restart(3.0)
    safe_audit(db, getattr(user, "username", "?"), "panel.restart", "ovpnmanager", "scheduled")
    return {"ok": True, "message": "面板将在约 3 秒后重启，请稍后刷新页面重新登录。"}

@router.get("/backups/{name}/download", dependencies=[Depends(ensure_superuser)])
def download_backup(name: str):
    """下载一个备份包。

    安全要点：``name`` 来自 URL，必须严格匹配由 backup-now 生成的文件名，再做一次
    realpath 包含性检查——否则 ``../`` 就能读走服务器上的任意文件（备份包里是 CA 私钥，
    泄露等于所有客户端证书作废）。
    """
    import re
    from pathlib import Path

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    if not re.fullmatch(r"manual-\d{8}-\d{6}\.tar\.gz", name):
        raise HTTPException(status_code=400, detail="备份文件名不合法")

    backup_dir = Path(BACKUP_DIR).resolve()
    target = (backup_dir / name).resolve()
    if backup_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(
        str(target), media_type="application/gzip", filename=name
    )

class BackupSchedulePayload(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    keep: int | None = Field(default=None, ge=1, le=200)


@router.get("/backup-schedule", dependencies=[Depends(ensure_superuser)])
def read_backup_schedule() -> dict:
    from app.services import backup_scheduler

    return backup_scheduler.load()


@router.put("/backup-schedule", dependencies=[Depends(ensure_superuser)])
def update_backup_schedule(payload: BackupSchedulePayload) -> dict:
    from app.services import backup_scheduler

    return backup_scheduler.save(**payload.model_dump(exclude_none=True))


@router.post("/backup-schedule/run-now", dependencies=[Depends(ensure_superuser)])
def run_backup_schedule_now() -> dict:
    from app.services import backup_scheduler

    return backup_scheduler.run_backup_once()
