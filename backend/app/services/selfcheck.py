"""面板内一键自检。

把 verify.sh 里那些"出问题才想起来要跑"的检查搬到界面上。设计约束：

* **每一项都必须独立容错**：某一项探不到（权限不足、命令不存在）只让该项变成
  warn/unknown，绝不能让整个自检 500——否则最需要诊断的时候它先挂了。
* **不给建议就不算诊断**：每项除了状态，还要带一句"该怎么办"。
* 只读：不重启、不改配置。
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import subprocess
import socket
from pathlib import Path

from app.core.config import get_settings
from app.services import management, openvpn as openvpn_service, server_conf

logger = logging.getLogger(__name__)


def _item(key: str, name: str, status: str, detail: str, hint: str = "") -> dict:
    return {"key": key, "name": name, "status": status, "detail": detail, "hint": hint}


def _unit_state(unit: str) -> str:
    """`systemctl is-active <unit>`。

    刻意不用 ``openvpn_service.service_details()``：那个函数只查固定的 OpenVPN 单元，
    不接受参数，也拿不到 nginx / apply.socket 的状态。
    """
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return proc.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _check_service(unit: str, label: str, key: str) -> dict:
    state = _unit_state(unit)
    if state == "active":
        return _item(key, label, "ok", "active")
    if not state:
        return _item(key, label, "unknown", "无法获取状态（systemd 不可用或无权限）",
                     "确认面板运行在有 systemd 的机器上")
    return _item(key, label, "fail", f"状态为 {state}",
                 f"systemctl status {unit} 查看原因；可尝试重启该服务")


def _check_port(port: int) -> dict:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.bind(("127.0.0.1", port))
        return _item("udp_port", "OpenVPN 端口", "fail", f"UDP {port} 未被监听",
                     "服务可能没起来；检查 openvpn@server 状态与 server.conf 的 port")
    except OSError as exc:
        # 端口被占用时：非特权进程绑定已占用端口抛 EADDRINUSE，绑定 <1024 抛 EACCES。
        # 两种情况都说明"有人在监听"。只有别的错误才是真的探不到。
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            return _item("udp_port", "OpenVPN 端口", "ok", f"UDP {port} 已在监听")
        return _item("udp_port", "OpenVPN 端口", "unknown", f"无法探测：{exc}", "")


def _check_ip_forward() -> dict:
    try:
        value = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
    except OSError as exc:
        return _item("ip_forward", "IP 转发", "unknown", f"读不到：{exc}", "")
    if value == "1":
        return _item("ip_forward", "IP 转发", "ok", "已开启")
    return _item("ip_forward", "IP 转发", "fail", "未开启（客户端无法访问外网）",
                 "在「服务端设置 → 网络转发」里打开")


def _check_managed_block() -> dict:
    try:
        conf = server_conf.load()
        text = Path(get_settings().server_conf_path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return _item("managed_block", "托管配置块", "unknown", f"读不到 server.conf：{exc}", "")
    if server_conf.MANAGED_BEGIN in text and server_conf.MANAGED_END in text:
        return _item("managed_block", "托管配置块", "ok", f"存在（{conf.port}/{conf.proto}）")
    return _item("managed_block", "托管配置块", "warn", "未找到托管标记块",
                 "在面板里保存一次「服务端设置」即可自动补上")


def _check_helper_channel() -> dict:
    state = _unit_state("apply.socket")
    if state == "active":
        return _item("helper_channel", "特权助手通道", "ok", "apply.socket active")
    if not state:
        return _item("helper_channel", "特权助手通道", "unknown", "无法获取状态", "")
    return _item("helper_channel", "特权助手通道", "fail", f"apply.socket {state}",
                 "所有修改配置的功能都会失败；重跑安装脚本可修复")


def _check_management() -> dict:
    try:
        clients = management.list_online(force=True)
        return _item("management", "管理接口", "ok", f"可用（在线 {len(clients)}）")
    except Exception as exc:  # noqa: BLE001
        return _item("management", "管理接口", "warn", f"不可用：{exc}",
                     "在线列表/流量会缺失；确认 server.conf 里有 management 并重启")


def _check_disk() -> dict:
    try:
        usage = shutil.disk_usage("/")
    except OSError as exc:
        return _item("disk", "磁盘空间", "unknown", f"探测失败：{exc}", "")
    percent = usage.used / usage.total * 100 if usage.total else 0
    detail = f"已用 {percent:.1f}%（剩 {usage.free // (1024 ** 3)} GiB）"
    if percent >= 95:
        return _item("disk", "磁盘空间", "fail", detail, "清理日志或扩容，否则证书与备份会写不进去")
    if percent >= 85:
        return _item("disk", "磁盘空间", "warn", detail, "建议清理 /var/log 与旧备份")
    return _item("disk", "磁盘空间", "ok", detail)


def _check_tls_key() -> dict:
    base = Path(get_settings().openvpn_base_path)
    for path in (base / "ta.key", base / "server" / "ta.key"):
        if path.is_file():
            if os.access(path, os.R_OK):
                return _item("tls_key", "tls-crypt 密钥", "ok", f"{path} 可读")
            return _item("tls_key", "tls-crypt 密钥", "fail", f"{path} 面板读不到",
                         "客户端配置导出会失败；应设为 root:面板用户 0640")
    return _item("tls_key", "tls-crypt 密钥", "unknown", "未找到 ta.key（可能用 tls-auth 或别的路径）", "")


def _check_cert_expiry() -> dict:
    try:
        from app.api.api_v1.endpoints.dashboard import _expiring_certificates
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            items = _expiring_certificates(db, 30)
    except Exception as exc:  # noqa: BLE001
        return _item("cert_expiry", "证书到期", "unknown", f"查询失败：{exc}", "")
    if not items:
        return _item("cert_expiry", "证书到期", "ok", "未来 30 天内无证书到期")
    expired = sum(1 for i in items if i["expired"])
    if expired:
        return _item("cert_expiry", "证书到期", "fail", f"{expired} 张已过期，共 {len(items)} 张需处理",
                     "吊销并重新签发这些客户端")
    return _item("cert_expiry", "证书到期", "warn", f"{len(items)} 张将在 30 天内到期",
                 "提前重新签发，避免同事突然连不上")


def run_all() -> dict:
    """执行全部检查；任何一项异常都只影响该项。"""
    checks = [
        lambda: _check_service("openvpn@server", "OpenVPN 服务", "openvpn_service"),
        lambda: _check_service("ovpnmanager", "面板服务", "panel_service"),
        lambda: _check_service("nginx", "nginx", "nginx"),
        _check_helper_channel,
        _check_ip_forward,
        _check_managed_block,
        _check_management,
        lambda: _check_port(int(server_conf.load().port or 1194)),
        _check_tls_key,
        _check_cert_expiry,
        _check_disk,
    ]
    items: list[dict] = []
    for factory in checks:
        try:
            items.append(factory())
        except Exception as exc:  # noqa: BLE001
            logger.debug("自检项失败", exc_info=True)
            items.append(_item("unknown", "未知检查项", "unknown", str(exc), ""))
    counts = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    summary = "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    return {"summary": summary, "counts": counts, "items": items}
