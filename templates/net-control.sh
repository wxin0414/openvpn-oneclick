#!/usr/bin/env bash
#
# 网络控制：ip_forward 开关 + NAT 出口网卡（由 apply.socket 激活、以 root 运行）
#
# 为什么单独一个助手
# -----------------
# 这两项既不属于 server.conf（不进托管块），也不是纯面板设置（要动内核参数与
# iptables），所以单开一个动作，候选文件仍然是固定的那个路径。
#
# 安全设计
# --------
# 1) EGRESS_IF 只能取**真实存在的接口**（/sys/class/net 下有名），并且必须确实
#    有默认路由 —— 否则改完客户端立刻没有出网，而重启 OpenVPN 也发现不了
#    （服务本身是好的，坏的只是转发链路）。
# 2) 改 NAT 时先按旧值删规则、再按新值加，最后用 iptables -C 确认规则真的在；
#    不成立就恢复旧规则并返回失败。
# 3) 持久化单元 ovpn-oneclick-nat.service 与安装脚本保持同一份格式。
#
# 候选文件（面板以 0600 写）
# -------------------------
#   IP_FORWARD=0|1
#   EGRESS_IF=<网卡名>            # 可空：空表示保持当前
set -Eeuo pipefail

PANEL_CONF="${PANEL_CONF:-/etc/ovpn-oneclick/panel.conf}"

if [ -r "$PANEL_CONF" ]; then
  # shellcheck disable=SC1090
  . "$PANEL_CONF" || true
fi

# 注意顺序：panel.conf 里也有一个 CANDIDATE（指向 panel.candidate），所以必须在
# 加载配置**之后**再设自己的候选路径，否则会被它覆盖，助手就去读错的候选文件了。
CANDIDATE="${NET_CANDIDATE:-/opt/ovpnmanager/backend/data/net.candidate}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ovpn-oneclick}"
SERVER_CONF="${SERVER_CONF:-/etc/openvpn/server.conf}"
NAT_UNIT="${NAT_UNIT:-/etc/systemd/system/ovpn-oneclick-nat.service}"
SYSCTL_CONF="${SYSCTL_CONF:-/etc/sysctl.d/99-ovpn-oneclick.conf}"

fail() { printf 'RESULT=failed\nIP_FORWARD=\nEGRESS_IF=\nREASON=%s\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "必须以 root 运行（通过 socket 激活）"
[ -f "$CANDIDATE" ] || fail "候选文件不存在：$CANDIDATE"

want_forward="$(sed -n 's/^IP_FORWARD=//p' "$CANDIDATE" | head -1 | tr -d '\r[:space:]')"
want_if="$(sed -n 's/^EGRESS_IF=//p' "$CANDIDATE" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
rm -f "$CANDIDATE"

case "$want_forward" in 0|1) ;; *) fail "IP_FORWARD 只能是 0 或 1，收到：${want_forward:-<空>}" ;; esac

# --- 校正网段（NAT 需要） ----------------------------------------------------
vpn_net="$(grep -oP '^\s*server\s+\K[0-9.]+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
vpn_prefix="$(grep -oP '^\s*server\s+[0-9.]+\s+\K[0-9.]+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
[ -n "$vpn_net" ] && [ -n "$vpn_prefix" ] || fail "无法从 $SERVER_CONF 读出 server 网段"
case "$vpn_prefix" in
  255.0.0.0) cidr=8 ;; 255.255.0.0) cidr=16 ;; 255.255.255.0) cidr=24 ;;
  255.255.254.0) cidr=23 ;; 255.255.252.0) cidr=22 ;; 255.255.248.0) cidr=21 ;;
  *) fail "不支持的掩码：$vpn_prefix" ;;
esac
net_cidr="${vpn_net}/${cidr}"

# --- 当前状态 ---------------------------------------------------------------
old_if="$(iptables -t nat -S POSTROUTING 2>/dev/null | sed -n 's/.*-s [0-9./]* -o \([^ ]*\) -j MASQUERADE.*/\1/p' | head -1 || true)"
if [ -z "$old_if" ] && [ -f "$NAT_UNIT" ]; then
  old_if="$(grep -oP '^IF="\K[^"]+' "$NAT_UNIT" 2>/dev/null | head -1 || true)"
fi
[ -n "$old_if" ] || old_if="$(ip -4 route ls default 2>/dev/null | grep -oP '(?<=dev )[^ ]+' | head -1 || true)"

new_if="${want_if:-$old_if}"

# --- 校验出口网卡 -----------------------------------------------------------
if [ -n "$want_if" ]; then
  [ -d "/sys/class/net/$new_if" ] || fail "接口不存在：$new_if"
  if ! ip -4 route ls default 2>/dev/null | grep -q "dev $new_if"; then
    fail "接口 $new_if 上没有默认路由，改过去会让客户端失去出网"
  fi
fi

# --- 应用 ip_forward --------------------------------------------------------
cur_forward="$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)"
if [ "$want_forward" = "1" ]; then
  mkdir -p "$(dirname "$SYSCTL_CONF")"
  printf 'net.ipv4.ip_forward=1\n' > "$SYSCTL_CONF"
  sysctl -qw net.ipv4.ip_forward=1 2>/dev/null || true
else
  rm -f "$SYSCTL_CONF"
  sysctl -qw net.ipv4.ip_forward=0 2>/dev/null || true
fi
now_forward="$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)"
[ "$now_forward" = "$want_forward" ] || fail "设置 ip_forward=$want_forward 失败（当前 $now_forward）"

# 关掉转发就等于断掉已连客户端的出网，这是明确的危险，必须让调用方知道
if [ "$want_forward" = "0" ]; then
  printf 'RESULT=ok\nIP_FORWARD=0\nEGRESS_IF=%s\nWARNING=%s\nREASON=\n' \
    "$new_if" "ip_forward 已关闭：已连接的客户端将失去出网（除非你自己做了其它转发）"
  exit 0
fi

# --- 重建 NAT 规则（先删旧，再加新，最后校验） -----------------------------
mkdir -p "$BACKUP_DIR"; chmod 0700 "$BACKUP_DIR" 2>/dev/null || true
stamp="$(date +%Y%m%d-%H%M%S)"
cp -a "$NAT_UNIT" "${BACKUP_DIR}/nat.unit.${stamp}.bak" 2>/dev/null || true

old_rules="$(iptables -t nat -S POSTROUTING 2>/dev/null | grep 'MASQUERADE' || true)"

restore_old_rules() {
  # 简单可靠的回滚：清掉我们加的所有 MASQUERADE，再把旧规则原样加回
  # 只动本网段的规则：这台机器上可能还有别人的 NAT，不能全删
  iptables -t nat -S POSTROUTING 2>/dev/null | grep 'MASQUERADE' | grep -F -- "-s ${net_cidr} " | \
    sed 's/^-A /-D /' | while IFS= read -r del; do
      # shellcheck disable=SC2086
      iptables -t nat $del 2>/dev/null || true
    done
  printf '%s\n' "$old_rules" | while IFS= read -r add; do
    [ -n "$add" ] || continue
    # shellcheck disable=SC2086
    iptables -t nat $add 2>/dev/null || true
  done
}

# 删掉针对本网段（以及旧网卡）的 MASQUERADE
iptables -t nat -S POSTROUTING 2>/dev/null | grep 'MASQUERADE' | grep -F -- "-s ${net_cidr} " | \
  sed 's/^-A /-D /' | while IFS= read -r del; do
    # shellcheck disable=SC2086
    iptables -t nat $del 2>/dev/null || true
  done

if ! iptables -t nat -A POSTROUTING -s "$net_cidr" -o "$new_if" -j MASQUERADE 2>/dev/null; then
  restore_old_rules
  fail "添加 NAT 规则失败（已恢复原规则）"
fi
if ! iptables -t nat -C POSTROUTING -s "$net_cidr" -o "$new_if" -j MASQUERADE 2>/dev/null; then
  restore_old_rules
  fail "NAT 规则校验失败（已恢复原规则）"
fi

# --- 持久化 ----------------------------------------------------------------
mkdir -p "$(dirname "$NAT_UNIT")"
cat > "$NAT_UNIT" <<EOF
[Unit]
Description=ovpn-oneclick NAT (MASQUERADE for ${net_cidr} via ${new_if})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
IF="${new_if}"
NET="${net_cidr}"
ExecStart=/bin/sh -c 'iptables -t nat -C POSTROUTING -s \${NET} -o \${IF} -j MASQUERADE || iptables -t nat -A POSTROUTING -s \${NET} -o \${IF} -j MASQUERADE'
ExecStop=/bin/sh -c 'iptables -t nat -D POSTROUTING -s \${NET} -o \${IF} -j MASQUERADE || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable ovpn-oneclick-nat.service >/dev/null 2>&1 || true

printf 'RESULT=ok\nIP_FORWARD=%s\nEGRESS_IF=%s\nNET=%s\nWARNING=\nREASON=\n' \
  "$now_forward" "$new_if" "$net_cidr"
# 记录最终状态，供面板读取（不依赖 root 的只读快照）
STATE_FILE="${NET_STATE_FILE:-/etc/ovpn-oneclick/net.state}"
{
  printf 'IP_FORWARD="%s"\n' "$now_forward"
  printf 'EGRESS_IF="%s"\n' "$new_if"
  printf 'NET="%s"\n' "$net_cidr"
  printf 'APPLIED_AT="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$STATE_FILE" 2>/dev/null && chmod 0644 "$STATE_FILE" 2>/dev/null || true
