"""服务端设置读写接口：让 server.conf 里的可编辑项都能在面板里改。

安全模型（为什么不是直接写 server.conf）
---------------------------------------
面板以**非 root** 用户运行，``server.conf`` 归 root。因此这里的分工是：

1. 面板把"想改成的样子"（完整候选配置）写到自己的数据目录
   ``<panel_data>/server.conf.candidate``（0600）；
2. 面板执行 ``sudo -n /usr/local/lib/ovpnmanager/apply-config``（**不带任何参数**，
   路径来自面板环境变量 ``APPLY_CONFIG_BIN``，默认值与 sudoers 模板一致）；
3. root 助手复核候选（托管块白名单 + "不得引入新的非托管指令"），备份、原子替换、
   按需重建 NAT/nginx、重启服务并等待就绪，失败自动回滚；
4. 面板解析助手 stdout 的最后几行（``RESULT=``/``BACKUP=``/``RESTARTED=``/
   ``ROLLED_BACK=``/``NETWORK_CHANGED=``/``REASON=``）后返回给前端。

面板自己**永远不写** ``server.conf``，也不重启服务——它没有被授权做这些事。
渲染与校验在 ``app/services/server_conf_writer.py`` 里（纯函数，可单测）。
"""

from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.core.config import get_settings
from app.services import client_tasks, server_conf
from app.services import certs as certs_service
from app.services.audit import safe_audit
from app.services.server_conf_writer import (
    APPLY_CONFIG_BIN,
    APPLY_TIMEOUT_SECONDS,
    build_candidate,
    candidate_path,
    validate_candidate,
    write_candidate,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# 改了这些项就必须重启 OpenVPN 服务：
# * port / proto / server / topology：监听端口、协议、tun 网段在配置解析期生效；
# * mssfix / sndbuf / rcvbuf / keepalive / client-to-client / max-clients /
#   duplicate-cn：其实可以由 management 接口在运行时改，但面板刻意不保留
#   management 长连接（少一个攻击面），所以一律按"需要重启"处理；
# * block-ipv6 是 push 出去的，只影响新连接，不需要重启。
# 原则：拿不准就算 true —— 重启是无害的（助手自己会重启并回滚），
# 漏报一步（该重启没重启）才会让面板显示的值和服务端实际行为不一致。
RESTART_SCOPED_KEYS = frozenset(
    {
        "port",
        "proto",
        "network",
        "netmask",
        "topology",
        "mssfix",
        "sndbuf",
        "rcvbuf",
        "keepalive",
        "client_to_client",
        "max_clients",
        "duplicate_cn",
    }
)

# 改了这些项要提醒重新导出 .ovpn：
# 它们要么直接进客户端配置（dns/tunnel_mode/routes/block_ipv6 是服务端 push 的），
# 要么客户端连接参数变了（port/proto）——旧 .ovpn 连不上或拿不到新推送。
CLIENT_AFFECTING_KEYS = frozenset(
    {"port", "proto", "dns_servers", "tunnel_mode", "routes", "block_ipv6"}
)

_HELPER_KEYS = (
    "RESULT",
    "BACKUP",
    "RESTARTED",
    "ROLLED_BACK",
    "NETWORK_CHANGED",
    "REASON",
    "ERROR_AT_LINE",
)

_MISSING_HELPER_MESSAGE = (
    "无法调用特权助手 {binary}：{reason}。"
    "该功能要求面板以非 root 用户运行、由 root 助手落地 server.conf，"
    "请确认面板是通过 install.sh 安装的"
    "（它会安装 {binary} 并在 sudoers 里为面板用户放行这一条免密命令），"
    "或检查 APPLY_CONFIG_BIN 是否指向了错误的路径。"
)
_SUDO_REFUSED_MESSAGE = (
    "sudo 拒绝以 root 执行特权助手 {binary}：{reason}。"
    "请确认面板是通过 install.sh 安装的（它会写入 sudoers 白名单），"
    "并确认面板用户被允许免密执行该命令。"
)
_HELPER_BAD_OUTPUT_MESSAGE = (
    "特权助手 {binary} 返回了无法解析的输出：{reason}。"
    "请确认安装的助手版本与面板匹配（install.sh 会同时安装两者），"
    "否则面板无法判断配置是否已经落地。"
)


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def _current_settings() -> dict:
    """当前 server.conf 里可编辑项的取值（供 GET 展示与 PATCH 比对）。"""
    conf = server_conf.load()
    return server_conf.editable_settings(conf, server_conf.parse_push_directives(conf.push_directives))


def _require_conf() -> server_conf.ServerConf:
    conf = server_conf.load()
    if not conf.exists:
        raise HTTPException(
            status_code=404,
            detail=f"找不到服务端配置 {conf.path}，请确认面板与 OpenVPN 装在同一台机器上",
        )
    return conf


@router.get("/server", summary="读取 server.conf 中可编辑的服务端设置")
def get_server_settings(user=Depends(get_current_user)) -> dict:
    """读取可编辑/只读的服务端设置（超级管理员）。

    返回的结构直接对应前端设置页：``settings`` 可改，``readonly`` 只展示。
    ``managed_block_present`` 说明配置里是否已有面板托管块（迁移过来的旧配置
    第一次保存前会是 false）。
    """
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    conf = _require_conf()
    pushes = server_conf.parse_push_directives(conf.push_directives)
    text = server_conf.read_text() or ""
    return {
        "path": conf.path,
        "exists": conf.exists,
        "settings": server_conf.editable_settings(conf, pushes),
        "readonly": server_conf.readonly_settings(conf),
        "managed_block_present": server_conf.managed_block_present(text),
    }


# --------------------------------------------------------------------------- #
# 助手调用
# --------------------------------------------------------------------------- #
def _parse_helper_output(stdout: str, stderr: str = "") -> dict[str, str]:
    """解析助手的 ``KEY=VALUE`` 输出（stdout；stderr 里出现同样的键也认）。

    助手用 ``printf`` 逐行输出，因此一行一个键。但失败原因（``REASON=``）本身
    可能含换行，所以这里只把"已知键开头的行"当成新字段，其余行接到上一个值后面。
    """
    parsed: dict[str, str] = {}
    last_key: str | None = None
    for raw in (stdout or "").splitlines() + (stderr or "").splitlines():
        line = raw.rstrip("\r")
        key, sep, value = line.partition("=")
        if sep and key in _HELPER_KEYS:
            parsed[key] = value
            last_key = key
        elif last_key and line.strip():
            parsed[last_key] = f"{parsed[last_key]}\n{line}"
    return parsed


def _run_helper() -> dict[str, str]:
    """以 sudo 执行 root 助手（不带任何参数），返回解析后的结果。

    助手不存在 / sudo 未授权 / 超时都会转成 500 + 中文说明；
    调用方不需要再区分这些失败形态。
    """
    cmd = ["sudo", "-n", APPLY_CONFIG_BIN]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=APPLY_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        # sudo 本身不存在（开发机/Windows）或助手路径不存在
        raise HTTPException(
            status_code=500,
            detail=_MISSING_HELPER_MESSAGE.format(binary=APPLY_CONFIG_BIN, reason=f"命令不存在（{exc.filename}）"),
        ) from exc
    except subprocess.TimeoutExpired as exc:
        # 注意：超时后面板无法知道助手是否已经把配置落地（它可能仍在后台完成），
        # 因此必须把候选文件留在原地并如实告知，不要把状态说成"没生效"。
        raise HTTPException(
            status_code=500,
            detail=(
                f"特权助手 {APPLY_CONFIG_BIN} 在 {APPLY_TIMEOUT_SECONDS} 秒内没有返回，"
                "无法确认配置是否已经落地。请查看助手日志与 systemctl status 后再重试。"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=_MISSING_HELPER_MESSAGE.format(binary=APPLY_CONFIG_BIN, reason=str(exc)),
        ) from exc

    parsed = _parse_helper_output(result.stdout, result.stderr)
    stderr = (result.stderr or "").strip()
    if not parsed.get("RESULT"):
        # 三种情况要分开报：sudo 拒绝（未配 NOPASSWD / 需要密码）、
        # 助手被 install.sh 装到了别处、助手版本不对（输出协议不匹配）
        reason = stderr or (result.stdout or "").strip() or "没有可解析的输出"
        if stderr and "sudo" in stderr.lower():
            raise HTTPException(
                status_code=500,
                detail=_SUDO_REFUSED_MESSAGE.format(binary=APPLY_CONFIG_BIN, reason=reason),
            )
        if "command not found" in reason.lower() or "no such file" in reason.lower():
            raise HTTPException(
                status_code=500,
                detail=_MISSING_HELPER_MESSAGE.format(binary=APPLY_CONFIG_BIN, reason=reason),
            )
        raise HTTPException(
            status_code=500,
            detail=_HELPER_BAD_OUTPUT_MESSAGE.format(binary=APPLY_CONFIG_BIN, reason=reason),
        )
    return parsed


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def _changed_keys(payload: dict, current: dict) -> list[str]:
    """返回确实发生变化的键（按请求里的顺序）。"""
    changed: list[str] = []
    for key in server_conf.EDITABLE_KEYS:
        if key not in payload:
            continue
        if payload[key] != current.get(key):
            changed.append(key)
    return changed


def _apply_message(
    *, restarted: bool, restart_required: bool, clients_affected: bool, ignored_routes: bool
) -> str:
    if restarted:
        message = "已应用并重启服务。"
    elif restart_required:
        message = "配置已应用，但助手没有重启服务，请手动重启 OpenVPN 使其生效。"
    else:
        message = "配置已应用，无需重启服务（改动只影响新建连接）。"
    if clients_affected:
        message += "端口/协议/推送变化会影响已发出的客户端配置，请用下方按钮重新导出。"
    if ignored_routes:
        message += "当前为全局代理模式（full），routes 不会下发给客户端，已忽略。"
    return message


@router.patch("/server", summary="修改 server.conf 中可编辑的服务端设置")
def update_server_settings(
    payload: dict = Body(...),
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> dict:
    """修改服务端设置并交给 root 助手落地（超级管理员）。

    严格校验：未知键、越界值、非法网段一律 400 并给出具体中文原因；
    校验通过后写候选文件 → 校验候选 → sudo 调用助手 → 解析结果。
    """
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    conf = _require_conf()
    current = _current_settings()

    problems = server_conf.validate_editable(payload, current=current)
    if problems:
        raise HTTPException(status_code=400, detail="；".join(problems))

    normalized = server_conf.normalize_editable(payload, current=current)

    # 托管块必须完整：未提交的项沿用当前值，否则会把当前配置里的指令"改没了"
    merged = dict(current)
    merged.update(normalized)
    # tunnel_mode=full 时 routes 会被忽略（由 redirect-gateway 决定路由方式），
    # 但仍如实回显，避免前端把用户填的路由清掉。注意"被忽略"的 routes 不算改动，
    # 否则响应会说"路由变了"而服务端其实一个字节都没动。
    ignored_routes = merged.get("tunnel_mode") == "full" and bool(merged.get("routes"))
    if ignored_routes:
        merged["routes"] = []
        normalized.pop("routes", None)

    changed = _changed_keys(normalized, current)
    if not changed:
        return {
            "ok": True,
            "changed": [],
            "restart_required": False,
            "restarted": False,
            "clients_affected": False,
            "backup": None,
            "network_changed": False,
            "message": "设置没有变化，未做任何修改。",
        }

    text = server_conf.read_text()
    if text is None:
        raise HTTPException(
            status_code=404,
            detail=f"找不到服务端配置 {conf.path}，请确认面板与 OpenVPN 装在同一台机器上",
        )

    candidate = build_candidate(text, merged)
    # 必须带上当前配置：第 4 条规则（托管块之外不得引入新指令）与 root 助手的
    # comm -13 判定完全一致，漏传会让"面板自检通过、助手拒绝"变成必然
    candidate_problems = validate_candidate(candidate, current_text=text)
    if candidate_problems:
        # 走到这里说明面板自己的渲染逻辑有 bug（或配置里混入了不支持的行）：
        # 拒绝下发，绝不把可疑内容交给 root 助手
        logger.error("候选配置未通过自检，拒绝下发：%s", candidate_problems)
        raise HTTPException(
            status_code=500,
            detail="生成的候选配置未通过安全自检，已拒绝下发：" + "；".join(candidate_problems),
        )

    path = candidate_path()
    try:
        write_candidate(path, candidate)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入候选配置失败（{path}）：{exc}") from exc

    helper = _run_helper()
    restart_required = bool(set(changed) & RESTART_SCOPED_KEYS)
    clients_affected = bool(set(changed) & CLIENT_AFFECTING_KEYS)
    restarted = helper.get("RESTARTED") == "yes"
    rolled_back = helper.get("ROLLED_BACK") == "yes"
    backup = helper.get("BACKUP") or None
    reason = helper.get("REASON", "")
    # 助手会自己判定网段是否变化（它要据此重建 NAT/nginx）；取它的结论，
    # 助手没输出这一项时退回面板自己的判断
    network_changed = (
        helper["NETWORK_CHANGED"] == "yes"
        if "NETWORK_CHANGED" in helper
        else bool(set(changed) & {"network", "netmask", "topology"})
    )
    actor = getattr(user, "username", "unknown")

    if helper.get("RESULT") != "ok":
        logger.error(
            "应用服务端设置失败 changed=%s reason=%s rolled_back=%s backup=%s",
            changed,
            reason,
            rolled_back,
            backup,
        )
        safe_audit(db, actor, "settings_apply", "server.conf", f"fail:changed={','.join(changed)}")
        detail = f"应用配置失败：{reason or '助手未给出原因'}"
        if rolled_back:
            backup_hint = f"，备份：{backup}" if backup else ""
            detail += f"（已回滚到上一份配置{backup_hint}）"
        detail += "。server.conf 未被改动或已回滚，请修正后重试。"
        raise HTTPException(status_code=500, detail=detail)

    # 配置变了，之前的解析缓存必须失效（mtime 通常也变了，但不依赖它）
    server_conf.invalidate()
    logger.info(
        "已应用服务端设置 changed=%s restarted=%s backup=%s network_changed=%s",
        changed,
        restarted,
        backup,
        network_changed,
    )
    safe_audit(db, actor, "settings_apply", "server.conf", f"success:changed={','.join(changed)}")
    return {
        "ok": True,
        "changed": changed,
        "restart_required": restart_required,
        "restarted": restarted,
        "clients_affected": clients_affected,
        "backup": backup,
        "network_changed": network_changed,
        "message": _apply_message(
            restarted=restarted,
            restart_required=restart_required,
            clients_affected=clients_affected,
            ignored_routes=ignored_routes,
        ),
    }


# --------------------------------------------------------------------------- #
# 重新导出全部客户端配置
# --------------------------------------------------------------------------- #
def _has_active_client_cert(client) -> bool:
    """证书未被吊销（没有证书记录的客户端也允许导出——导出只读 PKI 文件）。"""
    cert = getattr(client, "certificate", None)
    if cert is None or getattr(cert, "status", None) is None:
        return True
    return cert.status != "revoked"


@router.post("/reexport-all", summary="按当前服务端设置重新导出全部客户端 .ovpn")
def reexport_all_clients(
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> dict:
    """重新导出所有未吊销客户端的 .ovpn（超级管理员，同步执行）。

    端口/协议/DNS/路由变化后，已发出的 .ovpn 要么连不上、要么拿不到新推送，
    因此提供这个一次性按钮。导出只读 PKI 与 ta.key（不调用 easyrsa/openssl），
    所以同步执行是安全的；仍然逐个捕获异常，避免一个坏客户端让整批失败。
    """
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")

    host, port = client_tasks.server_endpoint(db)
    exported = 0
    failed: list[dict[str, str]] = []

    clients = db.query(crud.client.model).order_by(crud.client.model.id).all()
    for client in clients:
        if getattr(client, "disabled", False) or not _has_active_client_cert(client):
            continue
        try:
            certs_service.export_ovpn(client, remote_host=host, remote_port=port)
            exported += 1
        except Exception as exc:  # noqa: BLE001 - 单个客户端失败不应中断整批
            logger.warning("重新导出失败 cn=%s：%s", client.common_name, exc)
            failed.append({"name": client.common_name, "error": str(exc)})

    actor = getattr(user, "username", "unknown")
    safe_audit(
        db,
        actor,
        "settings_reexport_all",
        "clients",
        f"success:exported={exported},failed={len(failed)}",
    )
    logger.info("重新导出全部客户端：成功 %s，失败 %s", exported, len(failed))
    return {"ok": True, "exported": exported, "failed": failed}


__all__ = ["router"]
