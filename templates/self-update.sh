#!/usr/bin/env bash
#
# 在线升级（由 apply.socket 激活、以 root 运行）
#
# 为什么必须异步
# --------------
# 升级会重启面板自身，任何同步请求都会被切断。所以本脚本只做一件事：
# 起一个**脱离会话**的后台进程去跑安装器的 --update，然后立刻返回 STARTED。
# 进度由后台进程写 /etc/ovpn-oneclick/upgrade.state，面板轮询那个文件。
#
# 为什么"下载并执行"是可控的
# --------------------------
# 这是本项目唯一会执行远端代码的动作，因此限制很死：
#   1) URL 写死在 /etc/ovpn-oneclick/panel.conf（root 所有），面板不能传；
#   2) 下载后用标记自检：必须含 SCRIPT_VERSION= 与 ovpn-oneclick，否则拒绝执行；
#   3) 只调用 `--update`（安装器自己的升级路径，内部会先 backup_current）；
#   4) 全程日志写 /var/log/ovpn-oneclick-upgrade.log，失败原因写进状态文件。
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
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick}"

fail() { printf 'RESULT=failed\nREASON=%s\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
command -v curl >/dev/null 2>&1 || fail "找不到 curl"

current="$(grep -oP '^INSTALLED_VERSION="\K[^"]+' /etc/ovpn-oneclick/state.conf 2>/dev/null | head -1 || echo unknown)"

mkdir -p "$WORK_DIR"; chmod 0700 "$WORK_DIR" 2>/dev/null || true
tmp_installer="${WORK_DIR}/install.sh"

# 下载（固定 URL、跟随重定向、10 分钟上限）
if ! curl -fsSL --max-time 600 -o "$tmp_installer" "$INSTALLER_URL" 2>>"$LOG_FILE"; then
  fail "下载安装脚本失败（$INSTALLER_URL）"
fi

# 标记自检：不是我们的安装器就拒绝执行
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

# 状态文件：立刻写成 running，让面板马上能读到
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

# 后台真正执行升级（setsid + nohup：脱离当前会话，面板重启也不受影响）
setsid nohup /bin/bash -c '
  set -o pipefail
  cd "'"$WORK_DIR"'" || exit 1
  echo "=== upgrade start $(date -u +%Y-%m-%dT%H:%M:%SZ) from '"$current"' to '"$new_version"' ===" >> "'"$LOG_FILE"'"
  if bash "'"$tmp_installer"'" --update --yes >> "'"$LOG_FILE"' 2>&1; then
    st=ok; reason=""
  else
    st=failed; reason="安装器 --update 返回非 0，详见 '"$LOG_FILE"'"
  fi
  {
    printf "STATUS=\"%s\"\n" "$st"
    printf "FROM=\"%s\"\n" "'"$current"'"
    printf "TO=\"%s\"\n" "'"$new_version"'"
    printf "STARTED_AT=\"%s\"\n" "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"
    printf "FINISHED_AT=\"%s\"\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf "LOG=\"%s\"\n" "'"$LOG_FILE"'"
    printf "REASON=\"%s\"\n" "$reason"
  } > "'"$STATE_FILE"'"
  chmod 0644 "'"$STATE_FILE"'" 2>/dev/null || true
  echo "=== upgrade end $st ===" >> "'"$LOG_FILE"'"
' >>"$LOG_FILE" 2>&1 &

printf 'RESULT=ok\nSTATUS=started\nFROM=%s\nTO=%s\nLOG=%s\nREASON=\n' \
  "$current" "$new_version" "$LOG_FILE"
