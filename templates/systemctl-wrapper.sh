#!/usr/bin/env bash
#
# systemctl 包装脚本：让面板以非 root 用户运行时，只能操作受控的 OpenVPN 服务。
#
# 由 ovpn-oneclick 安装到 /usr/local/lib/ovpnmanager/systemctl-wrapper，
# 配合 /etc/sudoers.d/ovpnmanager 的"仅允许这几条 systemctl 命令"规则使用。
# 面板通过 .env 里的 SYSTEMCTL_BIN 指向本脚本。
#
# 安全要点：严格校验参数，不接受任意 systemctl 子命令或任意 unit 名——
# 否则等于把 root 的 systemctl 全权限交出去。
set -Eeuo pipefail

SERVICE="${OVPN_SERVICE_NAME:-openvpn@server}"
SYSTEMCTL="${SYSTEMCTL_PATH:-/bin/systemctl}"
SUDO="${SUDO_BIN:-/usr/bin/sudo}"
[ -x "$SYSTEMCTL" ] || SYSTEMCTL="$(command -v systemctl)"
[ -x "$SUDO" ] || SUDO="$(command -v sudo)"

cmd="${1:-}"
shift || true

# 允许调用方显式带上服务名，但**只接受受控的那一个**。
# 面板的部分调用点会按 systemctl 的习惯把服务名传进来；早期版本这里一律拒绝，
# 结果 `is-active openvpn@server` 被拒 → 面板把正在运行的服务误报成 unknown。
# 安全边界不变：无论怎么调用，最终交给 systemctl 的 unit 永远是 $SERVICE。
require_expected_service() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      "$SERVICE") shift ;;
      *) echo "refused argument: $1（只允许受控服务 ${SERVICE}）" >&2; exit 2 ;;
    esac
  done
}

# 只读查询：**非 root 能查就直连**，查不到才退到 sudo 白名单。
#
# 为什么必须这样：非 root 用户查询服务状态本来就允许（polkit / D-Bus 只读访问），
# 根本不需要提权。而某些云厂商的安全组件会给进程施加 NoNewPrivileges + seccomp
# ——实测 unit 里明明写着 NoNewPrivileges=no、systemd 也报 no，
# 运行进程的 /proc/<pid>/status 却是 NoNewPrivs:1，此时**任何** sudo 调用都会失败：
#   sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
# 若只读查询也走 sudo，面板就会把正在运行的服务误报成 unknown（实测踩过）。
#
# 判据用 `show -p LoadState`：只要该 unit 读得到就说明直连可用，
# 与服务当前是 active 还是 inactive 无关（is-active 对未运行的 unit 返回非 0）。
readonly_exec() {
  if "$SYSTEMCTL" show -p LoadState --value "$SERVICE" >/dev/null 2>&1; then
    exec "$SYSTEMCTL" "$@"
  fi
  exec "$SUDO" -n "$SYSTEMCTL" "$@"
}

case "$cmd" in
  start|stop|restart)
    # 只有这几个动作真的需要 root
    require_expected_service "$@"
    exec "$SUDO" -n "$SYSTEMCTL" "$cmd" "$SERVICE"
    ;;
  is-active)
    require_expected_service "$@"
    readonly_exec is-active "$SERVICE"
    ;;
  show)
    # 只读查询：允许 -p <property> 与 --value，unit 名强制使用受控服务
    props=()
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -p)
          [ "$#" -ge 2 ] || { echo "refused: -p needs a value" >&2; exit 2; }
          props+=("-p" "$2"); shift 2 ;;
        --value)
          props+=("--value"); shift ;;
        "$SERVICE")
          shift ;;
        *)
          echo "refused argument: $1" >&2; exit 2 ;;
      esac
    done
    readonly_exec show "${props[@]}" "$SERVICE"
    ;;
  status)
    require_expected_service "$@"
    readonly_exec status --no-pager --full "$SERVICE"
    ;;
  *)
    echo "unsupported command: ${cmd:-<empty>}" >&2
    exit 2
    ;;
esac
