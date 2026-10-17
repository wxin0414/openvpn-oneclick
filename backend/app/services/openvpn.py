"""OpenVPN / systemd 服务控制。

关键修复
--------
原实现用 ``systemctl status`` 的**人类可读文本**来判断服务状态：

* ``systemctl status`` 对「未运行」的 unit 返回退出码 3，原实现把它当成失败抛异常，
  于是 ``_status_from_output`` 里判断 ``inactive``/``dead`` 的分支**永远不会执行**，
  面板上的「已停止」永远显示为 ``unknown``；
* 文本匹配 ``"active: active (running)"`` 还依赖 systemd 输出为英文，
  系统 locale 变化就会失效。

现在改用 ``systemctl show -p LoadState -p ActiveState -p SubState`` —— 机器可读、
不受本地化影响，而且比格式化整个 status 输出更快。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Literal

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 服务控制走 socket 激活的 root 助手（见 templates/service-control.sh）。
# 面板进程带 NoNewPrivileges=1，任何 sudo 都会被拒绝，所以必须优先走这条路；
# sudo 只作为没有 socket 的环境（例如手工在终端里跑）的回退。
SERVICE_HELPER_BIN = os.environ.get(
    "SERVICE_HELPER_BIN", "/usr/local/lib/ovpnmanager/service-control"
)
SERVICE_CANDIDATE = os.environ.get(
    "SERVICE_CANDIDATE", "/opt/ovpnmanager/backend/data/service.candidate"
)

ServiceAction = Literal["status", "start", "stop", "restart"]

_ACTIVE_STATES = {"active"}
_STOPPED_STATES = {"inactive", "failed", "deactivating"}


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout or settings.systemctl_timeout,
        check=False,
    )


def _systemctl(*args: str) -> list[str]:
    """构造 systemctl 调用。

    ``SYSTEMCTL_BIN`` 可以指向一个包装脚本，让面板以非 root 用户运行时，
    只被授权操作 openvpn 服务这一条命令（见 deploy/ 下的 sudoers 模板）。
    """
    return [settings.systemctl_bin, *args]


def service_details() -> dict[str, str]:
    """一次 ``systemctl show`` 取回 LoadState/ActiveState/SubState。"""
    result = _run(
        _systemctl(
            "show",
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            settings.openvpn_service_name,
        )
    )
    details: dict[str, str] = {}
    # 查询失败必须留下原因：_run 用的是 check=False，如果这里不显式判断，
    # 任何失败（包装脚本不可执行、sudo 被沙箱拦住、unit 名不对……）都会变成
    # 界面上一个毫无线索的 ``unknown``，排查成本极高（实测为此花了很多时间）。
    if result.returncode != 0 or not (result.stdout or "").strip():
        logger.warning(
            "查询 OpenVPN 服务状态失败：cmd=%r returncode=%s stdout=%r stderr=%r",
            " ".join(_systemctl("show", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                                settings.openvpn_service_name)),
            result.returncode,
            (result.stdout or "").strip()[:200],
            (result.stderr or "").strip()[:300],
        )
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key:
            details[key.strip()] = value.strip()
    return details


def service_state() -> str:
    """返回 ``running`` / ``stopped`` / ``unknown``（机器可读）。

    ``unknown`` 只在无法查询（systemd 不可用）或 unit 不存在时返回，
    不再因为「服务没在运行」而误报。
    """
    try:
        details = service_details()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("查询 OpenVPN 服务状态失败：%s", exc)
        return "unknown"

    load_state = details.get("LoadState", "")
    active_state = details.get("ActiveState", "")

    if load_state == "not-found" or not active_state:
        return "unknown"
    if active_state in _ACTIVE_STATES:
        return "running"
    if active_state in _STOPPED_STATES:
        return "stopped"
    return "unknown"


def service_status_text() -> str:
    """人类可读的 ``systemctl status`` 输出，仅用于界面展示。

    退出码 3（未运行）/ 4（未知）都属于正常结果，不再当成错误。
    """
    # 不要用 `systemctl status`：降权包装脚本只接受受控 unit，会拒绝 --no-pager，
    # 于是界面把 "refused argument: --no-pager" 当成状态文本显示（实测过）。
    # 改用 show 的属性拼装，既稳定又不依赖本地化输出。
    details = service_details()
    load = details.get("LoadState", "").strip()
    state = details.get("ActiveState", "").strip()
    sub = details.get("SubState", "").strip()
    if not state:
        return "status unavailable"
    text = f"{state}"
    if sub:
        text += f" ({sub})"
    if load and load != "loaded":
        text = f"{load} / {text}"
    return text


def service_action(action: Literal["start", "stop", "restart"]) -> str:
    """执行 start/stop/restart；仅这些动作的失败才视为错误。

    这里把 OSError（例如 systemctl 或降权包装脚本不存在 / 不可执行）也归一化成
    RuntimeError：否则它会以未捕获异常的形式冒到接口层，变成没有可读信息的 500
    traceback，而调用方本来只捕获 RuntimeError。
    """
    via_helper, helper_reason = control_unit(action, settings.openvpn_service_name)
    if via_helper:
        return helper_reason
    # 回退：直连 systemctl（要求当前进程有权限，通常只在非 systemd 加固环境成立）
    try:
        result = _run(_systemctl(action, settings.openvpn_service_name))
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"无法执行 {settings.systemctl_bin} {action}：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "OpenVPN service command failed")
    return result.stdout.strip() or "ok"


def control_unit(action: str, unit: str | None = None) -> tuple[bool, str]:
    """通过 root 助手操作受控 unit。

    返回 ``(是否已由助手处理, 说明/状态)``。助手不可用时返回 ``(False, 原因)``，
    由调用方决定是否回退到 sudo 路径。
    """
    from app.services import helper_client

    target = unit or settings.openvpn_service_name
    try:
        candidate = Path(SERVICE_CANDIDATE)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(f"ACTION={action}\nUNIT={target}\n", encoding="utf-8")
        os.chmod(candidate, 0o600)
    except OSError as exc:
        # 写不了候选文件（例如开发机上没有生产路径）：交给调用方回退到直连 systemctl，
        # 而不是在这里硬抛——否则在没有 socket 助手的环境里连"查看状态"都做不了。
        logger.warning("无法写服务控制候选文件（%s）：%s", SERVICE_CANDIDATE, exc)
        return False, f"无法写候选文件：{exc}"

    try:
        _rc, stdout, stderr = helper_client.run_helper(
            "service", SERVICE_HELPER_BIN, settings.systemctl_timeout + 30
        )
    except helper_client.HelperUnavailable as exc:
        return False, str(exc)

    parsed: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()

    if parsed.get("RESULT") != "ok":
        reason = parsed.get("REASON") or (stderr or "").strip()[:300] or "服务操作失败"
        raise RuntimeError(reason)
    state = parsed.get("STATE") or "ok"
    return True, f"{action} {target} -> {state}"


def control(action: ServiceAction) -> str:
    """统一入口，兼容原有 ``/openvpn/{action}`` 接口。"""
    if action == "status":
        return service_status_text()
    return service_action(action)


def apply_crl_change() -> str:
    """CRL 文件更新后的处理。

    默认**不重启服务**。OpenVPN 的 ``--crl-verify`` 会在 CRL 文件发生变化后的
    新连接握手中重新读取该文件，因此吊销立即对新连接生效，而已建立的会话
    不会被无差别断开。原实现在每次吊销/删除后都 ``systemctl restart``，
    会把**所有**在线用户踢下线。

    如果运维环境里的 OpenVPN 版本不重新读取 CRL，可设置
    ``REVOKE_RESTARTS_SERVICE=true`` 恢复旧行为。
    """
    if settings.revoke_restarts_service:
        logger.info("REVOKE_RESTARTS_SERVICE=true，重启 OpenVPN 使 CRL 生效")
        return service_action("restart")
    return "crl-reloaded"


__all__ = [
    "ServiceAction",
    "service_details",
    "service_state",
    "service_status_text",
    "service_action",
    "control",
    "apply_crl_change",
]
