#!/usr/bin/env python3
#
# 特权助手分发器（由 apply.socket 激活、以 root 运行、处理一个连接后退出）
#
# 为什么是 Python 而不是 bash
# ---------------------------
# systemd 通过 StandardInput=socket 传进来的 fd 是**非阻塞**的：
#   - bash 的 `read -t` 直接报 `read error: 0: Invalid argument`（EINVAL）
#   - bash 的普通 `read` 会立刻得到 EAGAIN 并被当成读失败，表现为连接被重置
# Python 里可以显式 os.set_blocking(0, True) 把 fd 设回阻塞，然后 os.read 会
# 正常等待，这是 inetd 风格分发器最稳的写法。
#
# 为什么需要它
# ------------
# 很多云厂商的机器会给面板进程额外施加 NoNewPrivileges + seccomp（unit 里写
# NoNewPrivileges=no、systemd 也报 no，进程 /proc/<pid>/status 仍是 NoNewPrivs:1），
# 此时面板内的**任何 sudo 都会失败**。而 systemd 以 root 启动 User=root 的服务
# 不需要"获取"权限，因此不受影响 —— 这就是绕开限制的关键。
#
# 协议
# ----
#   客户端发送：ACTION=panel-config\n  或  ACTION=server-conf\n
#   本脚本：动作白名单 → exec 对应助手（助手 stdout 直接回给客户端）
#
# 安全边界
# --------
# 1) 动作白名单写死在这里，不解析参数、不接受路径；
# 2) 助手本身仍独立校验候选文件（属主/权限/大小/无 NUL）与内容（白名单）；
# 3) 本脚本不执行客户端发来的任何其它内容，只 exec 固定路径的助手。
import os
import sys

LIB_DIR = "/usr/local/lib/ovpnmanager"
MAX_REQUEST_BYTES = 4096

HELPERS = {
    "ACTION=panel-config": f"{LIB_DIR}/apply-panel-config",
    "ACTION=server-conf": f"{LIB_DIR}/apply-config",
    # 一键备份：只读操作（产出 tar.gz），不改任何配置
    "ACTION=backup": f"{LIB_DIR}/backup-now",
    # 从备份恢复：危险操作，助手内部有"恢复前强制备份 + 整体移走 + 失败还原"
    "ACTION=restore": f"{LIB_DIR}/restore-backup",
    # 网络控制：ip_forward 开关 + NAT 出口网卡
    "ACTION=net": f"{LIB_DIR}/net-control",
}


def fail(message: str) -> "None":
    sys.stdout.write(f"RESULT=failed\nREASON={message}\n")
    sys.stdout.flush()
    sys.exit(1)


def read_request() -> str:
    # 关键：systemd 给的 socket fd 是非阻塞的，先设回阻塞再读
    try:
        os.set_blocking(0, True)
    except OSError:
        pass
    data = b""
    while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:
        chunk = os.read(0, 1024)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()


def main() -> "None":
    action = "".join(read_request().split())
    if not action:
        fail("没有读到动作（连接被关闭）")
    helper = HELPERS.get(action)
    if helper is None:
        fail(f"未知动作：{action}")
    if not os.access(helper, os.X_OK):
        fail(f"助手未安装或不可执行：{helper}")
    os.execv(helper, [helper])


if __name__ == "__main__":
    main()
