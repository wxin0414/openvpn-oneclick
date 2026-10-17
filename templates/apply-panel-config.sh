#!/usr/bin/env bash
#
# =============================================================================
#  apply-panel-config —— 面板自身网络与安全配置的受控特权助手
# =============================================================================
#
# 为什么需要它
# ------------
# 面板以**非 root** 用户运行（刻意的安全设计），而 nginx 站点配置归 root。
# 「面板设置」页面要能改监听端口 / 绑定域名 / 放行来源 / TLS，就不能让面板
# 自己写 /etc/nginx —— 于是沿用本项目已有的模式：
#
#   面板只负责**写出它想要的样子**（/opt/ovpnmanager/backend/data/panel.candidate），
#   再由这个助手以 root 身份校验、渲染、备份、nginx -t、reload，失败自动回滚。
#
# sudoers 里只放这一条、且**不允许任何参数**：
#   ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/apply-panel-config
# 路径全部来自 root 所有的 /etc/ovpn-oneclick/panel.conf，调用者影响不了。
#
# 与 apply-config 的关系
# ----------------------
# 两个助手**并列**，不互相调用、也不合并：
#   * apply-config       管 server.conf（OpenVPN 服务端）+ NAT + nginx 放行网段；
#   * apply-panel-config 管 nginx 站点配置（面板入口：端口/域名/放行/TLS）。
# 分开的好处是各自的 sudoers 授权只够改自己那一块，一边的校验漏洞不会自动
# 扩成另一边。两者共用同一份候选文件校验（apply-validate.sh 的
# candidate_file_problem）与同一份 nginx 渲染实现（panel-nginx.sh）。
#
# 落地顺序（任何一步失败都不会让用户"既进不去、又没回滚"）
# --------------------------------------------------------
#   校验候选 → （selfsigned 时）生成证书 → 备份现配置（保留最近 5 份）
#   → 渲染到临时文件 → nginx -t → 失败就恢复备份并 reload → 成功才覆盖
#   → systemctl reload nginx → 失败就恢复备份并 reload
#   → 等 nginx 变 active（WAIT_RELOAD_SECONDS）→ 写 panel.state → 删候选
#
# 输出协议（面板解析这些行；最后一行一定是 RESULT=ok 或 RESULT=failed）
# -------------------------------------------------------------------
#   RESULT=ok|failed
#   NEW_URL=https://example.com:8443/ovpnmanager/
#   PORT_CHANGED=yes|no
#   TLS=off|selfsigned|existing
#   BACKUP=/var/backups/ovpn-oneclick/panel/ovpnmanager.conf.bak.20260101-000000
#   REASON=中文原因（成功时为空）
#
# 日志里**绝不**打印私钥或口令内容：本助手只接触文本配置与证书路径。
# =============================================================================

set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"

# 校验规则库（可脱离 root 单测）与 nginx 渲染库（与 install.sh 共用同一份实现）
PANEL_VALIDATE_LIB="${PANEL_VALIDATE_LIB:-/usr/local/lib/ovpnmanager/panel-validate.sh}"
APPLY_VALIDATE_LIB="${APPLY_VALIDATE_LIB:-/usr/local/lib/ovpnmanager/apply-validate.sh}"
PANEL_NGINX_LIB="${PANEL_NGINX_LIB:-/usr/local/lib/ovpnmanager/panel-nginx.sh}"

for lib in "$PANEL_VALIDATE_LIB" "$APPLY_VALIDATE_LIB" "$PANEL_NGINX_LIB"; do
  if [ ! -r "$lib" ]; then
    printf 'RESULT=failed\nREASON=找不到依赖库 %s\n' "$lib"
    exit 2
  fi
done
# shellcheck disable=SC1090
. "$PANEL_VALIDATE_LIB"
# shellcheck disable=SC1090
. "$APPLY_VALIDATE_LIB"
# shellcheck disable=SC1090
. "$PANEL_NGINX_LIB"

# ---------------------------------------------------------------------------
# 输出协议
# ---------------------------------------------------------------------------
BACKUP=""
NEW_URL=""
PORT_CHANGED="no"
TLS_MODE="off"
REASON=""
ROLLED_BACK="no"
RELOADED="no"
STAMP="$(date +%Y%m%d-%H%M%S)"

emit() { printf '%s\n' "$*"; }

finish() { # finish ok|failed <reason>
  local status="$1"; shift || true
  REASON="${*:-}"
  emit "RESULT=${status}"
  emit "NEW_URL=${NEW_URL}"
  emit "PORT_CHANGED=${PORT_CHANGED}"
  emit "TLS=${TLS_MODE}"
  emit "BACKUP=${BACKUP}"
  emit "ROLLED_BACK=${ROLLED_BACK}"
  emit "RELOADED=${RELOADED}"
  emit "REASON=${REASON}"
  [ "$status" = "ok" ] && exit 0 || exit 1
}

fail() { finish failed "$@"; }

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  [ -n "${STATE_DIR:-}" ] && mkdir -p "$STATE_DIR" 2>/dev/null || true
  [ -n "${STATE_DIR:-}" ] && printf '%s\n' "$msg" >> "${STATE_DIR}/panel.log" 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
# 回滚：把备份恢复回去并 reload。任何失败路径都只走这一个实现。
# ---------------------------------------------------------------------------
do_rollback() {
  trap - ERR
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ] && [ -n "${NGINX_CONF:-}" ]; then
    if cp -a "$BACKUP" "$NGINX_CONF"; then
      log "已把 ${NGINX_CONF} 恢复为备份 ${BACKUP}"
    else
      log "恢复备份失败（${BACKUP} -> ${NGINX_CONF}），请立即人工检查"
    fi
    if nginx -t >/dev/null 2>&1; then
      systemctl reload "${NGINX_SERVICE:-nginx}" >/dev/null 2>&1 || true
    fi
  elif [ -n "${NGINX_CONF:-}" ] && [ -f "$NGINX_CONF" ]; then
    # 改动前本来没有站点配置：回滚 = 删掉刚写进去的那份
    rm -f "$NGINX_CONF"
    log "改动前不存在 ${NGINX_CONF}，已删除新生成的配置完成回滚"
  fi
  ROLLED_BACK="yes"
  return 0
}

on_err() {
  local line="$1"
  emit "ERROR_AT_LINE=${line}"
  do_rollback >/dev/null 2>&1 || true
  finish failed "助手在第 ${line} 行意外失败"
}
trap 'on_err $LINENO' ERR

# ---------------------------------------------------------------------------
# 0. 不接受任何参数（sudoers 允许多余参数会扩大攻击面）
# ---------------------------------------------------------------------------
if [ "$#" -ne 0 ]; then
  emit "RESULT=failed"
  emit "REASON=apply-panel-config 不接受任何参数（路径来自 ${PANEL_CONF}）"
  exit 2
fi

[ "$(id -u)" -eq 0 ] || { emit "RESULT=failed"; emit "REASON=必须以 root 运行（通过 sudoers 白名单调用）"; exit 2; }
[ -r "$PANEL_CONF" ] || { emit "RESULT=failed"; emit "REASON=找不到配置文件 ${PANEL_CONF}"; exit 2; }

# shellcheck disable=SC1090
. "$PANEL_CONF"

: "${NGINX_CONF:?panel.conf 缺少 NGINX_CONF}"
: "${STATE_DIR:?panel.conf 缺少 STATE_DIR}"
: "${CANDIDATE:?panel.conf 缺少 CANDIDATE}"
: "${TLS_DIR:?panel.conf 缺少 TLS_DIR}"
: "${NGINX_TEMPLATE:?panel.conf 缺少 NGINX_TEMPLATE}"
WEB_ROOT="${WEB_ROOT:-/var/www}"
WEB_NAME="${WEB_NAME:-ovpnmanager}"
PANEL_PORT="${PANEL_PORT:-8000}"
NGINX_SERVICE="${NGINX_SERVICE:-nginx}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick/panel}"
BACKUP_KEEP="${BACKUP_KEEP:-5}"
WAIT_RELOAD_SECONDS="${WAIT_RELOAD_SECONDS:-10}"
TLS_DIR="${TLS_DIR:-${STATE_DIR}/tls}"
STATE_FILE="${STATE_DIR}/panel.state"

log "开始应用面板设置：candidate=${CANDIDATE}"

# ---------------------------------------------------------------------------
# 1. 候选文件的文件级校验（与 apply-config 共用 candidate_file_problem）
#    存在 / 普通文件 / 非空 / ≤32KB / 无 NUL / 属主是面板用户 / 非组或其他可写
# ---------------------------------------------------------------------------
candidate_problem="$(candidate_file_problem "$CANDIDATE" "${PANEL_USER:-}" 32768)"
if [ -n "$candidate_problem" ]; then
  fail "$candidate_problem"
fi

# ---------------------------------------------------------------------------
# 2. OpenVPN 端口（面板端口不允许与它相同，否则 nginx 会抢不到端口）
# ---------------------------------------------------------------------------
OVPN_PORT=""
if [ -n "${SERVER_CONF:-}" ] && [ -f "$SERVER_CONF" ]; then
  OVPN_PORT="$(grep -E '^[[:space:]]*port[[:space:]]+' "$SERVER_CONF" 2>/dev/null \
    | tail -1 | awk '{print $2}' || true)"
  case "$OVPN_PORT" in
    ''|*[!0-9]*) OVPN_PORT="" ;;
  esac
fi
log "OpenVPN 端口：${OVPN_PORT:-未声明}"

# ---------------------------------------------------------------------------
# 3. 候选内容校验（键白名单 + 取值规则，规则在 panel-validate.sh 里）
# ---------------------------------------------------------------------------
content_problem="$(panel_candidate_problem "$CANDIDATE" "$OVPN_PORT")"
if [ -n "$content_problem" ]; then
  fail "$content_problem"
fi

LISTEN_PORT="$(panel_candidate_value "$CANDIDATE" LISTEN_PORT)"
SERVER_NAME="$(panel_candidate_value "$CANDIDATE" SERVER_NAME)"
ALLOW_MODE="$(panel_candidate_value "$CANDIDATE" ALLOW_MODE)"
ALLOW_CIDRS="$(panel_candidate_value "$CANDIDATE" ALLOW_CIDRS)"
TLS_MODE="$(panel_candidate_value "$CANDIDATE" TLS_MODE)"
TLS_CERT="$(panel_candidate_value "$CANDIDATE" TLS_CERT)"
TLS_KEY="$(panel_candidate_value "$CANDIDATE" TLS_KEY)"

[ -n "$SERVER_NAME" ] || SERVER_NAME="_"
log "候选：port=${LISTEN_PORT} name=${SERVER_NAME} allow=${ALLOW_MODE} tls=${TLS_MODE}"

# ---------------------------------------------------------------------------
# 4. VPN 网段（allow_mode=vpn 时要用它；取自 server.conf，天然跟随后者变化）
# ---------------------------------------------------------------------------
VPN_CIDR=""
if [ -n "${SERVER_CONF:-}" ] && [ -f "$SERVER_CONF" ]; then
  VPN_CIDR="$(server_cidr "$SERVER_CONF" 2>/dev/null || true)"
fi
if [ "$ALLOW_MODE" = "vpn" ] && [ -z "$VPN_CIDR" ]; then
  fail "放行来源选了「仅 VPN 网段」，但读不到 server.conf 里的 server 网段"
fi

# ---------------------------------------------------------------------------
# 5. 旧端口（用于判断 PORT_CHANGED；优先读 panel.state，读不到就看现配置）
# ---------------------------------------------------------------------------
OLD_PORT="$(panel_state_value "$STATE_FILE" PANEL_LISTEN_PORT)"
if [ -z "$OLD_PORT" ] && [ -f "$NGINX_CONF" ]; then
  OLD_PORT="$(grep -E '^[[:space:]]*listen[[:space:]]' "$NGINX_CONF" 2>/dev/null \
    | head -1 | awk '{print $2}' | tr -d ';' || true)"
  case "$OLD_PORT" in
    ''|*[!0-9]*) OLD_PORT="" ;;
  esac
fi
if [ -n "$OLD_PORT" ] && [ "$OLD_PORT" != "$LISTEN_PORT" ]; then
  PORT_CHANGED="yes"
fi

# ---------------------------------------------------------------------------
# 6. TLS：selfsigned 时由助手生成证书（私钥 0600、目录 0700）
# ---------------------------------------------------------------------------
TLS_CERT_PATH="${TLS_DIR}/panel-selfsigned.crt"
TLS_KEY_PATH="${TLS_DIR}/panel-selfsigned.key"
TLS_CN_FILE="${TLS_DIR}/panel-selfsigned.cn"

ensure_selfsigned() { # ensure_selfsigned <CN>
  local cn="$1" san current=""
  mkdir -p "$TLS_DIR"
  chmod 0700 "$TLS_DIR" 2>/dev/null || true
  chown root:root "$TLS_DIR" 2>/dev/null || true

  if [ -f "$TLS_CN_FILE" ]; then
    current="$(cat "$TLS_CN_FILE" 2>/dev/null || true)"
  fi
  if [ -s "$TLS_CERT_PATH" ] && [ -s "$TLS_KEY_PATH" ] && [ "$current" = "$cn" ]; then
    log "复用已有自签证书（CN=${cn}）"
    chmod 0644 "$TLS_CERT_PATH"; chmod 0600 "$TLS_KEY_PATH"
    TLS_CERT="$TLS_CERT_PATH"; TLS_KEY="$TLS_KEY_PATH"
    return 0
  fi

  case "$cn" in
    *:*) san="IP:${cn}" ;;
    *[0-9].[0-9]*) san="IP:${cn}" ;;
    *) san="DNS:${cn}" ;;
  esac

  log "生成自签证书（CN=${cn}）"
  # 先写临时文件再 mv：避免生成失败时留下半张证书让 nginx 起不来
  local tmp_key="${TLS_KEY_PATH}.new.$$" tmp_crt="${TLS_CERT_PATH}.new.$$"
  rm -f "$tmp_key" "$tmp_crt"
  # -addext 需要 OpenSSL 1.1.1+；老版本会失败，退化为不带 SAN 的证书
  if ! openssl req -x509 -newkey rsa:2048 -days 825 -nodes -sha256 \
        -subj "/CN=${cn}" -addext "subjectAltName=${san}" \
        -keyout "$tmp_key" -out "$tmp_crt" >/dev/null 2>&1; then
    if ! openssl req -x509 -newkey rsa:2048 -days 825 -nodes -sha256 \
          -subj "/CN=${cn}" -keyout "$tmp_key" -out "$tmp_crt" >/dev/null 2>&1; then
      rm -f "$tmp_key" "$tmp_crt"
      log "自签证书生成失败"
      return 1
    fi
  fi
  chmod 0600 "$tmp_key"
  chmod 0644 "$tmp_crt"
  mv -f "$tmp_key" "$TLS_KEY_PATH"
  mv -f "$tmp_crt" "$TLS_CERT_PATH"
  printf '%s\n' "$cn" > "$TLS_CN_FILE"
  chmod 0644 "$TLS_CN_FILE"
  TLS_CERT="$TLS_CERT_PATH"; TLS_KEY="$TLS_KEY_PATH"
  return 0
}

if [ "$TLS_MODE" = "selfsigned" ]; then
  CN="$(panel_default_host "$SERVER_NAME")"
  ensure_selfsigned "$CN" || fail "生成自签证书失败（目录 ${TLS_DIR}）"
fi

# ---------------------------------------------------------------------------
# 7. 备份现有 nginx 站点配置（保留最近 BACKUP_KEEP 份）
# ---------------------------------------------------------------------------
mkdir -p "$BACKUP_DIR"
chmod 0750 "$BACKUP_DIR" 2>/dev/null || true
if [ -f "$NGINX_CONF" ]; then
  BACKUP="${BACKUP_DIR}/$(basename "$NGINX_CONF").bak.${STAMP}"
  cp -a "$NGINX_CONF" "$BACKUP"
  log "已备份 ${NGINX_CONF} -> ${BACKUP}"
else
  # 现配置不存在：仍然允许落地（例如站点配置被手工删掉了），回滚时删掉新文件
  log "当前不存在 ${NGINX_CONF}，将直接创建（回滚=删除新文件）"
fi

# 清理旧备份：只保留最近 N 份
if [ -n "$BACKUP" ]; then
  ls -1t "${BACKUP_DIR}/$(basename "$NGINX_CONF").bak."* 2>/dev/null \
    | tail -n "+$((BACKUP_KEEP + 1))" \
    | while IFS= read -r old; do [ -n "$old" ] && rm -f "$old"; done || true
fi

# ---------------------------------------------------------------------------
# 8. 渲染新配置到临时文件并校验（**先 nginx -t，成功后才覆盖**）
#    这样"改了端口却起不来"永远不会落盘
# ---------------------------------------------------------------------------
TMP_CONF="${NGINX_CONF}.pending.${STAMP}"
if ! PANEL_LISTEN_PORT="$LISTEN_PORT" \
     PANEL_SERVER_NAME="$SERVER_NAME" \
     PANEL_ALLOW_MODE="$ALLOW_MODE" \
     PANEL_ALLOW_CIDRS="$ALLOW_CIDRS" \
     PANEL_TLS_MODE="$TLS_MODE" \
     PANEL_TLS_CERT="$TLS_CERT" \
     PANEL_TLS_KEY="$TLS_KEY" \
     PANEL_PORT="$PANEL_PORT" \
     VPN_CIDR="$VPN_CIDR" \
     WEB_ROOT="$WEB_ROOT" \
     WEB_NAME="$WEB_NAME" \
     panel_nginx_render "${NGINX_TEMPLATE:?panel.conf 缺少 NGINX_TEMPLATE}" "$TMP_CONF"; then
  rm -f "$TMP_CONF"
  log "渲染 nginx 配置失败"
  do_rollback
  fail "渲染 nginx 站点配置失败（模板 ${NGINX_TEMPLATE}），未改动现有配置"
fi
chown root:root "$TMP_CONF" 2>/dev/null || true
chmod 0644 "$TMP_CONF"

# nginx -t 校验的是**整个** nginx 配置，因此只能先把新文件放到最终位置再校验；
# 为了能回滚，第 7 步已经备份过旧配置（BACKUP），失败时 do_rollback 会恢复它。
mv -f "$TMP_CONF" "$NGINX_CONF"

if ! nginx -t >/dev/null 2>&1; then
  log "nginx -t 失败，回滚"
  do_rollback
  fail "新配置未通过 nginx -t 校验，已回滚到上一份配置"
fi

# ---------------------------------------------------------------------------
# 9. reload nginx，并等它稳定（reload 失败同样回滚）
# ---------------------------------------------------------------------------
if ! systemctl reload "$NGINX_SERVICE" >/dev/null 2>&1; then
  log "reload ${NGINX_SERVICE} 失败，回滚"
  do_rollback
  fail "reload ${NGINX_SERVICE} 失败，已回滚到上一份配置"
fi
RELOADED="yes"

state=""
i=0
while [ "$i" -lt "$WAIT_RELOAD_SECONDS" ]; do
  state="$(systemctl is-active "$NGINX_SERVICE" 2>/dev/null || true)"
  [ "$state" = "active" ] && break
  sleep 1
  i=$((i + 1))
done

if [ "$state" != "active" ]; then
  log "${NGINX_SERVICE} 未保持 active（当前 ${state}），回滚"
  do_rollback
  fail "nginx 在新配置下未保持运行（当前状态：${state}），已回滚到上一份配置"
fi

# ---------------------------------------------------------------------------
# 10. 落地成功：写 panel.state（安装脚本重装/升级时优先读它）
# ---------------------------------------------------------------------------
if [ "$TLS_MODE" = "selfsigned" ]; then
  TLS_CERT="$TLS_CERT_PATH"
  TLS_KEY="$TLS_KEY_PATH"
fi

panel_write_state "$STATE_FILE" \
  PANEL_LISTEN_PORT "$LISTEN_PORT" \
  PANEL_SERVER_NAME "$SERVER_NAME" \
  PANEL_ALLOW_MODE "$ALLOW_MODE" \
  PANEL_ALLOW_CIDRS "$ALLOW_CIDRS" \
  PANEL_TLS_MODE "$TLS_MODE" \
  PANEL_TLS_CERT "$TLS_CERT" \
  PANEL_TLS_KEY "$TLS_KEY" \
  PANEL_LAST_BACKUP "${BACKUP:-}"
log "已写入 ${STATE_FILE}"

# 新地址：优先用绑定域名，否则用本机第一个非回环地址
HOST="$(panel_default_host "$SERVER_NAME")"
NEW_URL="$(panel_url_build "$TLS_MODE" "$LISTEN_PORT" "$WEB_NAME" "$HOST")"
log "应用成功：${NEW_URL}（port_changed=${PORT_CHANGED}，备份=${BACKUP:-无}）"

# 候选是一次性请求：成功后删掉，避免"下次谁调用就再应用一遍"的陈旧意图
rm -f "$CANDIDATE" 2>/dev/null || true

finish ok ""
