#!/usr/bin/env bash
#
# 从备份恢复（由 apply.socket 激活、以 root 运行、处理一个连接后退出）
#
# 这是整个项目里最危险的操作：它会覆盖 PKI、server.conf、.env 与 app.db。
# 因此有三条硬约束：
#
#   1) 恢复前**强制再备份一次**当前状态。备份本身就是你要恢复的那份也没关系——
#      目的是留下"恢复前"这个时间点，出了问题还能回去。
#   2) 解包前把现有的四个目标**整体移走**（同一文件系统内 mv 是原子的），
#      而不是就地覆盖。这样任何一步失败都能整体搬回来。
#   3) 解包后必须重启 OpenVPN 并确认它**稳定 active**；失败则用第 2 步移走的那份
#      整体还原，再重启，最后如实返回 RESULT=failed。
#
# 为什么用 "候选文件" 而不是命令行参数
# ------------------------------------
# apply-dispatch 不接受任何参数（否则等于把 root 的任意路径交给面板）。要恢复哪一份
# 由面板写进固定路径的候选文件，助手从这里读，并且**只接受备份目录下的
# manual-*.tar.gz**——即使候选被篡改，也越不出这个范围。
set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"
CANDIDATE="${RESTORE_CANDIDATE:-/opt/ovpnmanager/backend/data/restore.candidate}"

if [ -r "$PANEL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$PANEL_CONF" || true
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick}"
PKI_DIR="${PKI_DIR:-/etc/openvpn/easy-rsa/pki}"
SERVER_CONF="${SERVER_CONF:-/etc/openvpn/server.conf}"
PANEL_ENV="${PANEL_ENV:-/opt/ovpnmanager/backend/.env}"
PANEL_DB="${PANEL_DB:-/opt/ovpnmanager/backend/data/app.db}"
OVPN_SERVICE="${OVPN_SERVICE:-openvpn@server}"
WAIT_ACTIVE_SECONDS="${WAIT_ACTIVE_SECONDS:-25}"
STABLE_ACTIVE_SECONDS="${STABLE_ACTIVE_SECONDS:-4}"

WORK=""        # 这一步的临时工作区（pre-restore 就放在这里）
PRE_BACKUP=""

fail() {
  printf 'RESULT=failed\nRESTORED=\nPRE_BACKUP=%s\nREASON=%s\n' "$PRE_BACKUP" "$*"
  exit 1
}

cleanup() { [ -n "$WORK" ] && rm -rf "$WORK" 2>/dev/null || true; }
trap cleanup EXIT

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
[ -f "$CANDIDATE" ] || fail "候选文件不存在：$CANDIDATE"

# --- 1. 读并严格校验目标备份 ------------------------------------------------
target="$(sed -n 's/^BACKUP=//p' "$CANDIDATE" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
rm -f "$CANDIDATE"
[ -n "$target" ] || fail "候选文件里没有 BACKUP="

case "$target" in
  "$BACKUP_DIR"/manual-*.tar.gz) ;;
  *) fail "只允许恢复 ${BACKUP_DIR}/manual-*.tar.gz，收到：${target}" ;;
esac
[ -f "$target" ] || fail "备份文件不存在：$target"
[ -s "$target" ] || fail "备份文件为空：$target"

# 归档必须真的含有我们要恢复的东西，否则拒绝（防止用一个无关 tar 包把系统清空）
# 注意：不要写成 `tar -tzf ... | grep -q ...`。grep -q 命中即退出，tar 会收到
# SIGPIPE（141），而本脚本开了 pipefail，于是整条管道被判成失败 —— 判断永远成立，
# 任何备份都会被拒。先取清单再在内存里匹配，规避这个问题（实测踩过）。
archive_listing="$(tar -tzf "$target" 2>/dev/null || true)"
case "$archive_listing" in
  *etc/openvpn*) ;;
  *) fail "该归档里没有 etc/openvpn，不像是本项目的备份" ;;
esac

# --- 2. 恢复前强制再备份当前状态 --------------------------------------------
manual_backup() {
  local out="$1"
  local targets=()
  [ -d "$PKI_DIR" ]     && targets+=("$PKI_DIR")
  [ -f "$SERVER_CONF" ] && targets+=("$SERVER_CONF")
  [ -f "$PANEL_ENV" ]   && targets+=("$PANEL_ENV")
  [ -f "$PANEL_DB" ]    && targets+=("$PANEL_DB")
  [ "${#targets[@]}" -gt 0 ] || return 1
  tar -czf "$out" -C / "${targets[@]}" 2>/dev/null && chmod 0600 "$out"
}

PRE_BACKUP="${BACKUP_DIR}/manual-prerestore-$(date +%Y%m%d-%H%M%S).tar.gz"
manual_backup "$PRE_BACKUP" || fail "恢复前的安全备份失败（拒绝继续）"

# --- 3. 把现有目标整体移走（可原子还原） -------------------------------------
WORK="$(mktemp -d /var/tmp/ovpn-restore.XXXXXX)"
STASH="${WORK}/pre-restore"
mkdir -p "$STASH"

move_aside() { # $1=源路径  $2=在 stash 里的相对名
  [ -e "$1" ] || return 0
  mkdir -p "$STASH/$(dirname "$2")"
  mv "$1" "$STASH/$2" || return 1
}

restore_stash() {
  # 把 stash 里的东西搬回原位（先删掉可能已解包出来的新文件）
  [ -d "$STASH" ] || return 0
  ( cd "$STASH" && find . -mindepth 1 -maxdepth 3 -print0 2>/dev/null | \
      while IFS= read -r -d '' item; do
        rel="${item#./}"
        case "$rel" in
          */*) continue ;;   # 只处理顶层，逐个搬
        esac
        case "$rel" in
          easy-rsa-pki)    rm -rf "$PKI_DIR";      mv "$STASH/$rel" "$PKI_DIR" ;;
          server.conf)     rm -f  "$SERVER_CONF";  mv "$STASH/$rel" "$SERVER_CONF" ;;
          panel.env)       rm -f  "$PANEL_ENV";    mv "$STASH/$rel" "$PANEL_ENV" ;;
          app.db*)         rm -f  "$PANEL_DB"*;    mv "$STASH/$rel" "$PANEL_DB" ;;
        esac
      done )
}

move_aside "$PKI_DIR"     "easy-rsa-pki" || fail "无法移走 $PKI_DIR"
move_aside "$SERVER_CONF" "server.conf"  || fail "无法移走 $SERVER_CONF"
move_aside "$PANEL_ENV"   "panel.env"    || fail "无法移走 $PANEL_ENV"
# SQLite 还有 -wal / -shm，一并移走，否则恢复出来的 db 会带着旧 wal
for suffix in "" "-wal" "-shm"; do
  move_aside "${PANEL_DB}${suffix}" "app.db${suffix}" || fail "无法移走 ${PANEL_DB}${suffix}"
done

# --- 4. 解包 -----------------------------------------------------------------
if ! tar -xzf "$target" -C / 2>/dev/null; then
  restore_stash
  fail "解包失败，已还原到恢复前的状态"
fi

# 解包后至少要有 server.conf 与 pki，否则说明这个包不完整
if [ ! -f "$SERVER_CONF" ]; then
  restore_stash
  fail "解包后没有 server.conf，已还原到恢复前的状态"
fi

# --- 5. 重启并确认稳定运行；失败则整体还原 -----------------------------------
service_ready() {
  local state stable=0 i=0
  while [ "$i" -lt "$WAIT_ACTIVE_SECONDS" ]; do
    state="$(systemctl is-active "$OVPN_SERVICE" 2>/dev/null || true)"
    case "$state" in
      active) stable=$((stable + 1)); [ "$stable" -ge "$STABLE_ACTIVE_SECONDS" ] && return 0 ;;
      failed) return 1 ;;
      *) stable=0 ;;
    esac
    sleep 1; i=$((i + 1))
  done
  return 1
}

systemctl restart "$OVPN_SERVICE" >/dev/null 2>&1 || true
if ! service_ready; then
  restore_stash
  systemctl restart "$OVPN_SERVICE" >/dev/null 2>&1 || true
  service_ready || true
  fail "恢复后 ${OVPN_SERVICE} 无法稳定运行，已还原到恢复前的状态"
fi

printf 'RESULT=ok\nRESTORED=%s\nPRE_BACKUP=%s\nREASON=\n' "$target" "$PRE_BACKUP"
