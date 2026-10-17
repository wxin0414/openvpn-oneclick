#!/usr/bin/env bash
#
# 在线升级（由 apply.socket 激活、以 root 运行）
#
# 为什么必须异步
# --------------
# 升级会重启面板自身，任何同步请求都会被切断。所以本脚本只做三件事：
# 下载安装脚本 → 标记自检 → 起一个脱离会话的后台工作进程（upgrade-worker），
# 然后立刻返回 STARTED。进度由工作进程写 upgrade.state，面板轮询那个文件。
#
# 为什么"下载并执行"是可控的
# --------------------------
# 这是本项目唯一会执行远端代码的动作，因此限制很死：
#   1) URL 写死在 /etc/ovpn-oneclick/panel.conf（root 所有），面板不能传；
#   2) 下载后做标记自检：必须含 SCRIPT_VERSION= 与 ovpn-oneclick，否则拒绝执行；
#   3) 只调用安装器的 `--update`（内部会先 backup_current）；
#   4) 工作进程是**独立脚本 + 环境变量传参**，不做任何字符串拼接 —— 上一版就是
#      在单引号/双引号里拼内联脚本，改一处就破坏语法，而后台进程语法错会直接退出、
#      连终态都写不了，面板于是永远停在"升级中"（实测踩过）。
set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"
if [ -r "$PANEL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$PANEL_CONF" || true
fi

STATE_FILE="${UPGRADE_STATE_FILE:-/etc/ovpn-oneclick/upgrade.state}"
LOG_FILE="${UPGRADE_LOG:-/var/log/ovpn-oneclick-upgrade.log}"
WORK_DIR="${UPGRADE_WORK_DIR:-/var/tmp/ovpn-upgrade}"
INSTALLER_URL="${INSTALLER_URL:-https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh}"
WORKER="${UPGRADE_WORKER:-/usr/local/lib/ovpnmanager/upgrade-worker}"

fail() { printf 'RESULT=failed\nREASON=%s\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
command -v curl >/dev/null 2>&1 || fail "找不到 curl"
[ -x "$WORKER" ] || fail "找不到升级工作进程：$WORKER"

current="$(grep -oP '^INSTALLED_VERSION="\K[^"]+' /etc/ovpn-oneclick/state.conf 2>/dev/null | head -1 || echo unknown)"

# 工作目录必须真的建出来；失败就明确报错，而不是让后台进程在暗处死掉
mkdir -p "$WORK_DIR" || fail "无法创建工作目录 $WORK_DIR"
chmod 0700 "$WORK_DIR" 2>/dev/null || true
tmp_installer="${WORK_DIR}/install.sh"

if ! curl -fsSL --max-time 600 -o "$tmp_installer" "$INSTALLER_URL" 2>>"$LOG_FILE"; then
  fail "下载安装脚本失败（$INSTALLER_URL）"
fi
if ! grep -q 'SCRIPT_VERSION=' "$tmp_installer" 2>/dev/null; then
  rm -f "$tmp_installer"
  fail "下载到的文件不像本项目的安装脚本（缺少 SCRIPT_VERSION）"
fi
if ! grep -q 'ovpn-oneclick' "$tmp_installer" 2>/dev/null; then
  rm -f "$tmp_installer"
  fail "下载到的文件不像本项目的安装脚本（缺少项目标识）"
fi
chmod 0755 "$tmp_installer"
new_version="$(grep -oP '^SCRIPT_VERSION="\K[^"]+' "$tmp_installer" | head -1 || echo unknown)"

# 先落 running，再起后台
{
  printf 'STATUS="running"\n'
  printf 'FROM="%s"\n' "$current"
  printf 'TO="%s"\n' "$new_version"
  printf 'STARTED_AT="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'FINISHED_AT=""\n'
  printf 'LOG="%s"\n' "$LOG_FILE"
  printf 'REASON=""\n'
} > "$STATE_FILE"
chmod 0644 "$STATE_FILE" 2>/dev/null || true

# 后台工作进程：参数走环境变量，不做字符串拼接
env \
  UPGRADE_FROM="$current" \
  UPGRADE_TO="$new_version" \
  UPGRADE_INSTALLER="$tmp_installer" \
  UPGRADE_STATE_FILE="$STATE_FILE" \
  UPGRADE_LOG="$LOG_FILE" \
  setsid nohup /bin/bash "$WORKER" >>"$LOG_FILE" 2>&1 &

printf 'RESULT=ok\nSTATUS=started\nFROM=%s\nTO=%s\nLOG=%s\nREASON=\n' \
  "$current" "$new_version" "$LOG_FILE"
