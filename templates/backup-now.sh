#!/usr/bin/env bash
#
# 一键备份（由 apply.socket 激活、以 root 运行、处理一个连接后退出）
#
# 备份什么
# --------
# 真正需要备份的是三样东西（缺任何一样都无法完整恢复）：
#   1) PKI 与 server.conf   —— /etc/openvpn（CA、服务端/客户端证书、吊销列表、配置）
#   2) .env                 —— SECRET_KEY 与初始口令（丢了等于所有人被登出）
#   3) app.db               —— 客户端记录与审计日志
#
# 不备份什么
# ----------
# 不备份面板代码与前端产物：它们可以从仓库重新安装；把代码混进备份只会让
# 备份体积变大、恢复时又不知道该不该覆盖。
#
# 输出（与其它助手一致的 KEY=VALUE 协议）
# --------------------------------------
#   RESULT=ok|failed
#   BACKUP=<生成的 tar.gz 绝对路径>
#   SIZE=<字节>
#   FILES=<打包的文件数>
#   REASON=<失败原因>
set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick}"
KEEP="${BACKUP_KEEP:-10}"
KEEP="${KEEP:-10}"

# 路径都可以用 panel.conf 覆盖，但都在 root 所有的文件里，调用方影响不到
if [ -r "$PANEL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$PANEL_CONF" || true
  BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick}"
fi

PKI_DIR="${PKI_DIR:-/etc/openvpn/easy-rsa/pki}"
SERVER_CONF="${SERVER_CONF:-/etc/openvpn/server.conf}"
PANEL_ENV="${PANEL_ENV:-/opt/ovpnmanager/backend/.env}"
PANEL_DB="${PANEL_DB:-/opt/ovpnmanager/backend/data/app.db}"

fail() {
  printf 'RESULT=failed\nBACKUP=\nSIZE=0\nFILES=0\nREASON=%s\n' "$*"
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
command -v tar >/dev/null 2>&1 || fail "找不到 tar"

# 至少要有一个关键路径存在，否则说明这台机器根本不是本项目的安装
found=0
for p in "$PKI_DIR" "$SERVER_CONF" "$PANEL_ENV" "$PANEL_DB"; do
  [ -e "$p" ] && found=$((found + 1))
done
[ "$found" -gt 0 ] || fail "四个关键路径都不存在，似乎不是本项目的安装"

mkdir -p "$BACKUP_DIR" || fail "无法创建备份目录 $BACKUP_DIR"
chmod 0700 "$BACKUP_DIR" 2>/dev/null || true

ts="$(date +%Y%m%d-%H%M%S)"
out="${BACKUP_DIR}/manual-${ts}.tar.gz"
tmp="${out}.tmp"

# 只打包存在的项（tar 遇到不存在的路径会返回非 0）
targets=()
[ -d "$PKI_DIR" ]    && targets+=("$PKI_DIR")
[ -f "$SERVER_CONF" ] && targets+=("$SERVER_CONF")
[ -f "$PANEL_ENV" ]   && targets+=("$PANEL_ENV")
[ -f "$PANEL_DB" ]    && targets+=("$PANEL_DB")

# -C / 让归档内是绝对路径，恢复时可以原位解开；备份文件本身 0600（含 CA 私钥与 SECRET_KEY）
if ! tar -czf "$tmp" -C / "${targets[@]/#/}" 2>/dev/null; then
  rm -f "$tmp"
  fail "打包失败"
fi
chmod 0600 "$tmp"
mv "$tmp" "$out"

size="$(stat -c '%s' "$out" 2>/dev/null || echo 0)"
files="$(tar -tzf "$out" 2>/dev/null | wc -l)"

# 只保留最近 KEEP 份手动备份（自动备份在别的目录，不受影响）
ls -1t "${BACKUP_DIR}"/manual-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
  rm -f "$old" 2>/dev/null || true
done

printf 'RESULT=ok\nBACKUP=%s\nSIZE=%s\nFILES=%s\nREASON=\n' "$out" "$size" "$files"
