#!/usr/bin/env bash
#
# systemctl 包装脚本：让面板以非 root 用户运行时，只能操作受控的 unit。
#
# 由 ovpn-oneclick 安装到 /usr/local/lib/ovpnmanager/systemctl-wrapper，
# 配合 /etc/sudoers.d/ovpnmanager 的"仅允许这几条 systemctl 命令"规则使用。
# 面板通过 .env 里的 SYSTEMCTL_BIN 指向本脚本。
#
# 受控 unit 只有两个（写死，调用者换不了）：
#   * OpenVPN 服务（$SERVICE，默认 openvpn@server）
#   * 面板自己的服务（$PANEL_UNIT，默认 ovpnmanager）——用于「面板设置」里的
#     「重启面板」按钮；不授权它就只能 SSH 上去重启，体验上缺一块。
#
# 安全要点：严格校验参数，不接受任意 systemctl 子命令或任意 unit 名——
# 否则等于把 root 的 systemctl 全权限交出去。
set -Eeuo pipefail

SERVICE="${OVPN_SERVICE_NAME:-openvpn@server}"
PANEL_UNIT="${PANEL_SERVICE_NAME:-ovpnmanager}"
SYSTEMCTL="${SYSTEMCTL_PATH:-/bin/systemctl}"
SUDO="${SUDO_BIN:-/usr/bin/sudo}"
[ -x "$SYSTEMCTL" ] || SYSTEMCTL="$(command -v systemctl)"
[ -x "$SUDO" ] || SUDO="$(command -v sudo)"

cmd="${1:-}"
shift || true

# 允许调用方显式带上 unit 名，但**只接受受控的那两个**；不带则默认 OpenVPN 服务。
# 早期版本这里一律拒绝任何参数，结果 `is-active openvpn@server` 被拒 →
# 面板把正在运行的服务误报成 unknown。安全边界不变：最终交给 systemctl 的
# unit 永远是 $SERVICE 或 $PANEL_UNIT 之一。
resolve_unit() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      "$SERVICE") shift ;;
      "$PANEL_UNIT") printf '%s' "$PANEL_UNIT"; shift ;;
      *) echo "refused argument: $1（只允许受控 unit ${SERVICE} / ${PANEL_UNIT}）" >&2; exit 2 ;;
    esac
  done
  return 0
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
readonly_exec() { # readonly_exec <unit> <systemctl 参数...>
  local unit="$1"; shift
  if "$SYSTEMCTL" show -p LoadState --value "$unit" >/dev/null 2>&1; then
    exec "$SYSTEMCTL" "$@" "$unit"
  fi
  exec "$SUDO" -n "$SYSTEMCTL" "$@" "$unit"
}

# 需要 root 的动作：只接受受控 unit
unit=""
case "$cmd" in
  start|stop|restart)
    unit="$(resolve_unit "$@")" || exit 2
    [ -n "$unit" ] || unit="$SERVICE"
    exec "$SUDO" -n "$SYSTEMCTL" "$cmd" "$unit"
    ;;
  is-active)
    unit="$(resolve_unit "$@")" || exit 2
    [ -n "$unit" ] || unit="$SERVICE"
    readonly_exec "$unit" is-active
    ;;
  show)
    # 只读查询：允许 -p <property> 与 --value，unit 名强制使用受控服务
    props=()
    unit="$SERVICE"
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
    readonly_exec "$unit" show "${props[@]}"
    ;;
  status)
    resolve_unit "$@" >/dev/null || exit 2
    readonly_exec "$SERVICE" status --no-pager --full
    ;;
  *)
    echo "unsupported command: ${cmd:-<empty>}" >&2
    exit 2
    ;;
esac
