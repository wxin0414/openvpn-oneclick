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

case "$cmd" in
  start|stop|restart)
    [ "$#" -eq 0 ] || { echo "refused: unexpected arguments: $*" >&2; exit 2; }
    exec "$SUDO" -n "$SYSTEMCTL" "$cmd" "$SERVICE"
    ;;
  is-active)
    [ "$#" -eq 0 ] || { echo "refused: unexpected arguments: $*" >&2; exit 2; }
    exec "$SUDO" -n "$SYSTEMCTL" is-active "$SERVICE"
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
    exec "$SUDO" -n "$SYSTEMCTL" show "${props[@]}" "$SERVICE"
    ;;
  status)
    exec "$SUDO" -n "$SYSTEMCTL" status --no-pager --full "$SERVICE"
    ;;
  *)
    echo "unsupported command: ${cmd:-<empty>}" >&2
    exit 2
    ;;
esac
