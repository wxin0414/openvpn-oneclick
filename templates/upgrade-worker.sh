#!/usr/bin/env bash
#
# 升级工作进程（由 self-update 用 setsid 起在后台，脱离会话）
#
# 参数全部走环境变量，**不做任何字符串拼接** —— 这是上一版失败的原因：
# 在 self-update 里用单引号/双引号拼接内联脚本，稍一改动就破坏语法，
# 而后台进程语法错会直接退出、连终态都写不了，面板就永远停在"升级中"。
#
# 输入（环境变量）
# ---------------
#   UPGRADE_FROM        升级前版本
#   UPGRADE_TO          目标版本
#   UPGRADE_INSTALLER   已下载并自检通过的安装脚本绝对路径
#   UPGRADE_STATE_FILE  状态文件
#   UPGRADE_LOG         日志文件
#
# 约定：无论成功、失败还是异常退出，都要留下终态。用 trap 兜住前两种情况。
set -uo pipefail

STATE_FILE="${UPGRADE_STATE_FILE:-/etc/ovpn-oneclick/upgrade.state}"
LOG_FILE="${UPGRADE_LOG:-/var/log/ovpn-oneclick-upgrade.log}"
FROM_VER="${UPGRADE_FROM:-unknown}"
TO_VER="${UPGRADE_TO:-unknown}"
INSTALLER="${UPGRADE_INSTALLER:-}"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished=0

write_state() { # $1=STATUS $2=REASON
  finished=1
  {
    printf 'STATUS="%s"\n' "$1"
    printf 'FROM="%s"\n' "$FROM_VER"
    printf 'TO="%s"\n' "$TO_VER"
    printf 'STARTED_AT="%s"\n' "$STARTED_AT"
    printf 'FINISHED_AT="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'LOG="%s"\n' "$LOG_FILE"
    printf 'REASON="%s"\n' "$2"
  } > "$STATE_FILE" 2>/dev/null || true
  chmod 0644 "$STATE_FILE" 2>/dev/null || true
}

on_exit() {
  local rc=$?
  if [ "$finished" -eq 0 ]; then
    write_state failed "升级进程异常退出（rc=${rc}），详见 ${LOG_FILE}"
  fi
}
trap on_exit EXIT

echo "=== upgrade start ${STARTED_AT} from ${FROM_VER} to ${TO_VER} ===" >> "$LOG_FILE" 2>/dev/null || true

if [ -z "$INSTALLER" ] || [ ! -f "$INSTALLER" ]; then
  write_state failed "找不到已下载的安装脚本：${INSTALLER:-<空>}"
  exit 1
fi

# 注意：不能用 `if cmd; then ...; fi` 之后再取 $? —— if 语句在条件为假时本身返回 0，
# 于是失败会被上报成"返回 0"（实测踩过）。这里直接取命令自己的退出码。
/bin/bash "$INSTALLER" --update --yes >> "$LOG_FILE" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  write_state ok ""
  echo "=== upgrade end ok $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG_FILE" 2>/dev/null || true
  exit 0
fi
write_state failed "安装器 --update 返回 ${rc}，详见 ${LOG_FILE}"
echo "=== upgrade end failed rc=${rc} ===" >> "$LOG_FILE" 2>/dev/null || true
exit "$rc"
