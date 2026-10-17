#!/usr/bin/env bash
#
# =============================================================================
#  apply-config —— ovpn-oneclick 的受控特权助手
# =============================================================================
#
# 为什么需要它
# ------------
# 面板以非 root 用户运行（这是刻意的安全设计），而 server.conf、NAT 规则、
# nginx 站点配置都归 root。要"所有设置都能在面板里改"，又不想把面板提权成 root，
# 就让面板只负责**写出它想要的配置**，再由这个助手以 root 身份校验并落地。
#
# sudoers 里只放这一条、且**不允许任何参数**：
#     ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/apply-config
# 路径全部来自 /etc/ovpn-oneclick/apply.conf，调用者无法影响。
#
# 安全边界（这是整个特性敢上线的理由）
# ------------------------------------
# 1) 候选文件必须存在、是普通文件、非空且不超过 128KB、不含 NUL；
# 2) 候选文件必须归面板用户所有，且不能是组/其他可写（防止本机其它用户投毒）；
# 3) 候选文件里"托管块"之外的指令，必须**全部已存在于当前 server.conf 中**
#    —— 也就是说面板只能修改托管块里的内容、以及把冲突的旧行注释掉，
#    不可能塞进 script-security / up / plugin 这类任意指令。用 comm -13 取
#    候选有而当前没有的行，必须为空；
# 4) 托管块内的指令名必须在白名单里，push 的内容也要逐个校验。
#
# 落地顺序与回滚
# --------------
# 备份 → 原子替换 server.conf → （网段变了就）重建 NAT 与 nginx 放行网段
# → 重启服务 → 轮询 is-active；任何一步失败或服务没起来，就整体回滚并重启回去。
# 助手会打印 RESULT=ok|failed 等一行行结果供面板解析。
# =============================================================================

set -Eeuo pipefail

APPLY_CONF="${APPLY_CONF:-/etc/ovpn-oneclick/apply.conf}"

# 校验规则与常量都在这个库里（能被测试单独 source 断言），本脚本只负责落地。
VALIDATE_LIB="${VALIDATE_LIB:-/usr/local/lib/ovpnmanager/apply-validate.sh}"
if [ ! -r "$VALIDATE_LIB" ]; then
  printf 'RESULT=failed\nREASON=找不到校验库 %s\n' "$VALIDATE_LIB"
  exit 2
fi
# shellcheck disable=SC1090
. "$VALIDATE_LIB"

# ---------------------------------------------------------------------------
# 输出协议：最后一行一定是 RESULT=ok 或 RESULT=failed
# ---------------------------------------------------------------------------
BACKUP=""
RESTARTED="no"
ROLLED_BACK="no"
NETWORK_CHANGED="no"
REASON=""

emit() { printf '%s\n' "$*"; }

finish() { # finish ok|failed <reason>
  local status="$1"; shift || true
  REASON="${*:-}"
  emit "RESULT=${status}"
  emit "BACKUP=${BACKUP}"
  emit "RESTARTED=${RESTARTED}"
  emit "ROLLED_BACK=${ROLLED_BACK}"
  emit "NETWORK_CHANGED=${NETWORK_CHANGED}"
  emit "REASON=${REASON}"
  [ "$status" = "ok" ] && exit 0 || exit 1
}

fail() { finish failed "$@"; }

# 任何未预料的错误也要给出 RESULT=failed，否则面板只看到"没有输出"
on_err() {
  local line="$1"
  emit "ERROR_AT_LINE=${line}"
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
    do_rollback >/dev/null 2>&1 || true
  fi
  finish failed "助手在第 ${line} 行意外失败"
}
trap 'on_err $LINENO' ERR

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  [ -n "${STATE_DIR:-}" ] && mkdir -p "$STATE_DIR" 2>/dev/null || true
  [ -n "${STATE_DIR:-}" ] && printf '%s\n' "$msg" >> "${STATE_DIR}/apply.log" 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
# 0. 不接受任何参数：sudoers 允许多余参数会扩大攻击面
# ---------------------------------------------------------------------------
if [ "$#" -ne 0 ]; then
  emit "RESULT=failed"
  emit "REASON=apply-config 不接受任何参数（路径来自 ${APPLY_CONF}）"
  exit 2
fi

[ "$(id -u)" -eq 0 ] || { emit "RESULT=failed"; emit "REASON=必须以 root 运行（通过 sudoers 白名单调用）"; exit 2; }
[ -r "$APPLY_CONF" ] || { emit "RESULT=failed"; emit "REASON=找不到配置文件 ${APPLY_CONF}"; exit 2; }

# shellcheck disable=SC1090
. "$APPLY_CONF"

: "${SERVER_CONF:?apply.conf 缺少 SERVER_CONF}"
: "${OVPN_SERVICE:?apply.conf 缺少 OVPN_SERVICE}"
: "${CANDIDATE:?apply.conf 缺少 CANDIDATE}"
WAIT_ACTIVE_SECONDS="${WAIT_ACTIVE_SECONDS:-20}"

log "开始应用：candidate=${CANDIDATE}"

# ---------------------------------------------------------------------------
# 1. 候选文件的基本校验
#    具体实现已抽到 apply-validate.sh 的 candidate_file_problem()，
#    与第二个特权助手 apply-panel-config **共用同一份代码**：
#    两个助手都信任面板写出的文件，判据必须一字不差。
# ---------------------------------------------------------------------------
candidate_problem="$(candidate_file_problem "$CANDIDATE" "${PANEL_USER:-}" 131072)"
[ -n "$candidate_problem" ] && fail "$candidate_problem"

[ -f "$SERVER_CONF" ] || fail "找不到当前 server.conf：${SERVER_CONF}"

# ---------------------------------------------------------------------------
# 2. 安全校验：托管块白名单 + "候选不得引入任何新的非托管指令"
#    具体实现见 apply-validate.sh（那几条规则是可单独测试的纯函数）
# ---------------------------------------------------------------------------

# 2a) 托管块内的指令名与 push 内容都必须在白名单里
block_reason=""
if ! block_reason="$(validate_managed_block "$CANDIDATE")"; then
  fail "${block_reason:-托管块校验失败}"
fi

# 2b) 关键：候选在托管块之外不得引入任何新指令
extra="$(unauthorized_extra "$SERVER_CONF" "$CANDIDATE")"
if [ -n "$extra" ]; then
  log "候选引入了未授权指令：$extra"
  fail "候选配置在托管块之外引入了未授权的指令：$(printf '%s' "$extra" | tr '\n' ';')"
fi

# 2c) 反向：候选也不得**删除**既有指令，只能把它们注释成退役行。
#     只做 2b 的话，"偷偷删掉 crl-verify 从而关掉证书吊销"这种降级查不出来。
while IFS= read -r lost; do
  [ -n "$lost" ] || continue
  if ! retired_as_comment "$CANDIDATE" "$lost"; then
    fail "候选移除了既有指令（只允许注释掉、不允许删除）：${lost}"
  fi
done < <(removed_lines "$SERVER_CONF" "$CANDIDATE")

# ---------------------------------------------------------------------------
# 3. 备份
# ---------------------------------------------------------------------------
BACKUP="${SERVER_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
cp -a "$SERVER_CONF" "$BACKUP"
log "已备份到 ${BACKUP}"

# ---------------------------------------------------------------------------
# 4. 网段变化检测（决定是否要一起改 NAT 与 nginx）
#    server_cidr / mask_to_cidr 来自 apply-validate.sh
# ---------------------------------------------------------------------------
OLD_CIDR="$(server_cidr "$SERVER_CONF")"
NEW_CIDR="$(server_cidr "$CANDIDATE")"
[ "$OLD_CIDR" = "$NEW_CIDR" ] || NETWORK_CHANGED="yes"
log "网段：${OLD_CIDR:-未声明} -> ${NEW_CIDR:-未声明}（变化=${NETWORK_CHANGED}）"

# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------
NGINX_BACKUP=""
do_rollback() {
  trap - ERR
  log "开始回滚"
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
    cp -a "$BACKUP" "$SERVER_CONF"
    log "已从 ${BACKUP} 恢复 server.conf"
  fi
  # nginx 放行网段回滚
  if [ -n "$NGINX_BACKUP" ] && [ -f "$NGINX_BACKUP" ] && [ -n "${NGINX_CONF:-}" ]; then
    cp -a "$NGINX_BACKUP" "$NGINX_CONF"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
    log "已恢复 nginx 配置"
  fi
  # NAT 回滚
  if [ "$NETWORK_CHANGED" = "yes" ] && [ -n "${NAT_SCRIPT:-}" ] && [ -f "$NAT_SCRIPT" ] && [ -n "$OLD_CIDR" ]; then
    if grep -qE '^NET=' "$NAT_SCRIPT"; then
      sed -i "s|^NET=.*|NET=\"${OLD_CIDR}\"|" "$NAT_SCRIPT"
      "$NAT_SCRIPT" >/dev/null 2>&1 || true
    fi
    log "已把 NAT 脚本的网段改回 ${OLD_CIDR}"
  fi
  systemctl restart "$OVPN_SERVICE" >/dev/null 2>&1 || true
  ROLLED_BACK="yes"
  log "回滚完成"
  return 0
}

# ---------------------------------------------------------------------------
# 5. 原子替换 server.conf（保持 root:root 0644）
# ---------------------------------------------------------------------------
tmp_conf="${SERVER_CONF}.new.$$"
cp "$CANDIDATE" "$tmp_conf"
chown root:root "$tmp_conf" 2>/dev/null || true
chmod 0644 "$tmp_conf"
mv -f "$tmp_conf" "$SERVER_CONF"
log "已写入 ${SERVER_CONF}"

# ---------------------------------------------------------------------------
# 6. 网段变了：重建 NAT 与 nginx 放行网段
# ---------------------------------------------------------------------------
if [ "$NETWORK_CHANGED" = "yes" ] && [ -n "$NEW_CIDR" ]; then
  if [ -n "${NAT_SCRIPT:-}" ] && [ -f "$NAT_SCRIPT" ]; then
    # 先删掉旧网段的 MASQUERADE 规则，避免留下垃圾规则
    if [ -n "$OLD_CIDR" ]; then
      egress="$(grep -oP '^IF="\K[^"]+' "$NAT_SCRIPT" 2>/dev/null | head -1 || true)"
      if [ -n "$egress" ]; then
        iptables -t nat -D POSTROUTING -s "$OLD_CIDR" -o "$egress" -j MASQUERADE 2>/dev/null || true
        log "已删除旧网段 NAT 规则：${OLD_CIDR} -> ${egress}"
      fi
    fi
    sed -i "s|^NET=.*|NET=\"${NEW_CIDR}\"|" "$NAT_SCRIPT"
    if ! "$NAT_SCRIPT" >/dev/null 2>&1; then
      do_rollback
      fail "重建 NAT 规则失败（新网段 ${NEW_CIDR}），已回滚"
    fi
    log "NAT 规则已按新网段 ${NEW_CIDR} 重建"
  else
    log "未配置 NAT_SCRIPT，跳过 NAT 重建（可能是分流模式）"
  fi

  if [ -n "${NGINX_CONF:-}" ] && [ -f "$NGINX_CONF" ]; then
    NGINX_BACKUP="${NGINX_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "$NGINX_CONF" "$NGINX_BACKUP"
    # 只改第一条 allow 行的网段，保持其它指令不动
    if grep -qE '^[[:space:]]*allow[[:space:]]' "$NGINX_CONF"; then
      sed -i "0,/^[[:space:]]*allow[[:space:]].*/s||    allow ${NEW_CIDR};|" "$NGINX_CONF"
      if ! nginx -t >/dev/null 2>&1; then
        do_rollback
        fail "按新网段更新 nginx 后配置校验失败，已回滚"
      fi
      systemctl reload nginx >/dev/null 2>&1 || true
      log "nginx 放行网段已改为 ${NEW_CIDR}"
    else
      log "nginx 配置里没有 allow 行，跳过"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 7. 重启并等待服务就绪；起不来就回滚
# ---------------------------------------------------------------------------
if ! systemctl restart "$OVPN_SERVICE" >/dev/null 2>&1; then
  do_rollback
  fail "重启 ${OVPN_SERVICE} 失败，已回滚到上一份配置"
fi
RESTARTED="yes"

# 就绪判定：必须**连续保持** active 若干秒，而不是"看到一次 active 就算成功"。
# 为什么：OpenVPN 在某些配置错误下（典型是 `user <不存在的人>`——降权发生在初始化
# 之后）会先进入 active 再退出。只看一次采样会把它判成成功，于是坏配置被留在盘上、
# 服务进入重启循环，而面板还显示"已应用"。
# 实测踩过这个坑：生产 VPN 直接不可用，且面板毫无提示。
STABLE_ACTIVE_SECONDS="${STABLE_ACTIVE_SECONDS:-4}"
state=""
stable=0
i=0
while [ "$i" -lt "$WAIT_ACTIVE_SECONDS" ]; do
  state="$(systemctl is-active "$OVPN_SERVICE" 2>/dev/null || true)"
  case "$state" in
    active)
      stable=$((stable + 1))
      [ "$stable" -ge "$STABLE_ACTIVE_SECONDS" ] && break
      ;;
    failed)
      break   # 已经是 failed，不必再等
      ;;
    *)
      stable=0
      ;;
  esac
  sleep 1
  i=$((i + 1))
done

if [ "$state" != "active" ] || [ "$stable" -lt "$STABLE_ACTIVE_SECONDS" ]; then
  log "服务未能稳定 active（状态 ${state}，连续 ${stable}s，要求 ${STABLE_ACTIVE_SECONDS}s），开始回滚"
  do_rollback
  fail "新配置下 ${OVPN_SERVICE} 未能稳定运行（状态 ${state}，连续 ${stable}s），已回滚到上一份配置"
fi

# ---------------------------------------------------------------------------
# 8. 记录结果
# ---------------------------------------------------------------------------
if [ -n "${STATE_DIR:-}" ]; then
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  {
    printf 'APPLIED_AT="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'BACKUP="%s"\n' "$BACKUP"
    printf 'OLD_CIDR="%s"\n' "$OLD_CIDR"
    printf 'NEW_CIDR="%s"\n' "$NEW_CIDR"
    printf 'OVPN_SERVICE="%s"\n' "$OVPN_SERVICE"
  } > "${STATE_DIR}/last-apply.conf" 2>/dev/null || true
fi

log "应用成功（备份 ${BACKUP}）"

# 候选是一次性请求：成功落地后删掉它。好处有两个：
# ① 不会留下一份"下次谁调用就再应用一遍"的陈旧意图；
# ② verify.sh 的实机冒烟（无候选时应被拒绝）才能保持无副作用。
rm -f "$CANDIDATE" 2>/dev/null || true

finish ok ""
