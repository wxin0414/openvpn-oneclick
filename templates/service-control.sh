#!/usr/bin/env bash
#
# 服务控制（start / stop / restart / status），由 apply.socket 激活、以 root 运行
#
# 为什么需要它
# ------------
# 面板进程带 NoNewPrivileges=1（即使 unit 里写了 NoNewPrivileges=no 也无效，
# systemd 会因其它加固选项强制该标志），因此面板里任何 `sudo` 都会被拒绝：
#     sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
# 实测表现就是「服务启停」按钮一律 500。配置写入早已改走 socket 激活绕开这个限制，
# 服务控制这条路径当时漏了，这里补上。
#
# 安全边界
# --------
# apply-dispatch 不接受任何参数（否则等于把 root 的 systemctl 全权限交出去）。
# 要做什么由面板写进固定路径的候选文件，本脚本从那里读，并且**只接受**：
#   * ACTION 属于 start / stop / restart / status
#   * UNIT 属于受控的两个 unit（OpenVPN 服务、面板自己的服务）
set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"
SERVICE="${OVPN_SERVICE_NAME:-openvpn@server}"
PANEL_UNIT="${PANEL_SERVICE_NAME:-ovpnmanager}"
SYSTEMCTL="${SYSTEMCTL_PATH:-/bin/systemctl}"
WAIT_ACTIVE_SECONDS="${WAIT_ACTIVE_SECONDS:-25}"
STABLE_ACTIVE_SECONDS="${STABLE_ACTIVE_SECONDS:-4}"

if [ -r "$PANEL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$PANEL_CONF" || true
fi

# 顺序要紧：panel.conf 里可能定义了同名变量（CANDIDATE 等），加载之后再定自己的
CANDIDATE="${SERVICE_CANDIDATE:-/opt/ovpnmanager/backend/data/service.candidate}"

fail() { printf 'RESULT=failed\nSTATE=\nREASON=%s\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
[ -f "$CANDIDATE" ] || fail "候选文件不存在：$CANDIDATE"
[ -x "$SYSTEMCTL" ] || SYSTEMCTL="$(command -v systemctl)" || fail "找不到 systemctl"

action="$(sed -n 's/^ACTION=//p' "$CANDIDATE" | head -1 | tr -d '\r[:space:]')"
unit="$(sed -n 's/^UNIT=//p' "$CANDIDATE" | head -1 | tr -d '\r[:space:]')"
rm -f "$CANDIDATE"

case "$action" in
  start|stop|restart|status) ;;
  *) fail "不支持的动作：${action:-<空>}" ;;
esac

# unit 白名单：空 = 默认 OpenVPN 服务
if [ -z "$unit" ]; then
  unit="$SERVICE"
fi
case "$unit" in
  "$SERVICE"|"$PANEL_UNIT") ;;
  *) fail "不允许操作 unit：${unit}（只允许 ${SERVICE} / ${PANEL_UNIT}）" ;;
esac

state_of() { "$SYSTEMCTL" is-active "$unit" 2>/dev/null || true; }

if [ "$action" = "status" ]; then
  printf 'RESULT=ok\nSTATE=%s\nREASON=\n' "$(state_of)"
  exit 0
fi

# start / restart 之后必须**连续** active，避免"起来了又马上挂"被当成成功
wait_ready() {
  local stable=0 i=0 state
  while [ "$i" -lt "$WAIT_ACTIVE_SECONDS" ]; do
    state="$(state_of)"
    case "$state" in
      active) stable=$((stable + 1)); [ "$stable" -ge "$STABLE_ACTIVE_SECONDS" ] && return 0 ;;
      failed) return 1 ;;
      *) stable=0 ;;
    esac
    sleep 1; i=$((i + 1))
  done
  return 1
}

if ! out="$("$SYSTEMCTL" "$action" "$unit" 2>&1)"; then
  # 停止场景下 is-active 返回非 0 是正常的，不强求
  if [ "$action" != "stop" ]; then
    fail "$("$SYSTEMCTL" "$action" "$unit" 2>&1 | tail -3 | tr '\n' ' ')"
  fi
fi

case "$action" in
  start|restart)
    if ! wait_ready; then
      fail "$unit 在 ${WAIT_ACTIVE_SECONDS} 秒内没有稳定 active（当前 $(state_of)）"
    fi
    ;;
  stop)
    # 停完确认真的不是 active
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      [ "$(state_of)" = "active" ] || break
      sleep 1
    done
    [ "$(state_of)" = "active" ] && fail "$unit 仍未停止"
    ;;
esac

printf 'RESULT=ok\nSTATE=%s\nREASON=\n' "$(state_of)"
