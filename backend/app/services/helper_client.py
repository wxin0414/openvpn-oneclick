"""特权助手的调用通道：**socket 激活优先，sudo 兜底**。

为什么不能只用 sudo
-------------------
很多云厂商的机器会给面板进程额外施加 ``NoNewPrivileges`` + seccomp。此时即使
unit 里明确写着 ``NoNewPrivileges=no``、``systemctl show`` 也报 ``no``，
进程的 ``/proc/<pid>/status`` 里仍然是 ``NoNewPrivs: 1``，于是面板内的**任何**
sudo 都会失败：

    sudo: The "no new privileges" flag is set, which prevents sudo from running as root.

这是环境层面的限制，从 unit 里关不掉（实测：unit 无 NoNewPrivileges、无 drop-in、
PID 1 的 NoNewPrivs 也是 0，面板进程仍是 1）。

解决方案：由 systemd 以 root 拉起助手
------------------------------------
``apply.socket``（``Accept=yes``）+ ``apply@.service``（``User=root``）：systemd 自己
启动一个 root 进程来服务连接，**不需要"获取"权限**，因此完全不受 NNP 影响。
面板只需连 Unix socket 发一行动作：

    ACTION=panel-config\\n   或   ACTION=server-conf\\n

为什么保留 sudo 兜底
------------------
没有被施加 NNP 的普通主机上 sudo 是好的，且老版本安装可能只有 sudoers 没有
socket 单元。两条通道并存互不影响，谁可用用谁。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess

logger = logging.getLogger(__name__)

# 与 templates/apply.socket 的 ListenStream 保持一致
HELPER_SOCKET = os.environ.get("HELPER_SOCKET", "/run/ovpn-oneclick/apply.sock")
SOCKET_CONNECT_TIMEOUT = float(os.environ.get("HELPER_SOCKET_CONNECT_TIMEOUT", "5"))

# Windows 没有 AF_UNIX（开发机上跑测试时会走到这里），此时直接跳过 socket 通道，
# 由 sudo 兜底（开发机上 sudo 通常也不存在，会抛 HelperUnavailable，测试按此断言即可）。
_SOCKET_FAMILY = getattr(socket, "AF_UNIX", None)


class HelperUnavailable(RuntimeError):
    """两条通道都不可用（既没有 socket，sudo 也失败）。"""


def _via_socket(action: str, timeout: int) -> tuple[int, str, str] | None:
    """经 socket 激活调用助手；通道不可用时返回 None（由调用方决定是否兜底）。"""
    if _SOCKET_FAMILY is None:
        return None
    try:
        sock = socket.socket(_SOCKET_FAMILY, socket.SOCK_STREAM)
    except OSError:
        return None
    try:
        # 连接超时要短：socket 不存在/没人监听时应尽快退回 sudo
        sock.settimeout(SOCKET_CONNECT_TIMEOUT)
        sock.connect(HELPER_SOCKET)
        # 连上之后助手可能要跑很久（改配置、重启服务），放宽到调用方给的超时
        sock.settimeout(timeout)
        sock.sendall(f"ACTION={action}\n".encode())
        chunks: list[bytes] = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
        stdout = b"".join(chunks).decode("utf-8", "replace")
        if not stdout.strip():
            logger.warning("socket 通道没有返回任何内容（action=%s）", action)
            return None
        # socket 通道能连上并返回，就以上面的 RESULT= 为准
        returncode = 0 if "RESULT=ok" in stdout else 1
        return returncode, stdout, ""
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError,
            socket.timeout, OSError) as exc:
        logger.info("socket 通道不可用（action=%s）：%s；将退回 sudo", action, exc)
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _via_sudo(binary: str, timeout: int) -> tuple[int, str, str]:
    """sudo 兜底通道。"""
    try:
        proc = subprocess.run(
            ["sudo", "-n", binary],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HelperUnavailable(f"找不到 sudo 或助手：{exc.filename}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HelperUnavailable(
            f"助手在 {timeout} 秒内没有返回，无法确认配置是否已生效"
        ) from exc
    except OSError as exc:
        raise HelperUnavailable(f"无法执行助手：{exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr


def run_helper(action: str, sudo_binary: str, timeout: int) -> tuple[int, str, str]:
    """调用特权助手。

    :param action: ``panel-config`` 或 ``server-conf``（与 apply-dispatch 的白名单一致）
    :param sudo_binary: sudo 兜底时执行的助手路径
    :param timeout: 助手最长运行时间（秒）
    :returns: ``(returncode, stdout, stderr)``；socket 通道的 returncode 由 ``RESULT=`` 决定
    :raises HelperUnavailable: 两条通道都不可用
    """
    result = _via_socket(action, timeout)
    if result is not None:
        return result
    return _via_sudo(sudo_binary, timeout)
