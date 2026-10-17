#!/usr/bin/env bash
#
# apply-config 的校验逻辑测试（不需要 root，开发机上就能跑）
#
# 为什么单独测这个：apply-config 以 root 运行、依赖 systemctl/nginx，没法在开发机上
# 端到端跑；但它**拒绝什么**才是"让面板改系统配置"这件事敢上线的原因，所以那几条规则
# 被拆成纯函数放进 templates/apply-validate.sh，这里逐条断言。
#
# 用法：bash tests/test_apply_config.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass=0; fail=0
ok()  { printf '  \033[1;32m[PASS]\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*"; fail=$((fail+1)); }
skip(){ printf '  \033[1;33m[SKIP]\033[0m %s\n' "$*"; }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
not() { ! "$@"; }
eq() { if [ "$2" = "$3" ]; then ok "$1（=$3）"; else bad "$1：实际 '$2'，期望 '$3'"; fi; }
contains()     { printf '%s' "$2" | grep -qE "$1"; }
not_contains() { ! printf '%s' "$2" | grep -qE "$1"; }

# shellcheck disable=SC1090
. "${ROOT}/templates/apply-validate.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

printf '\n\033[1;34m== 掩码与网段解析 ==\033[0m\n'
eq "255.255.255.0 -> 24"   "$(mask_to_cidr 255.255.255.0)" "24"
eq "255.255.0.0 -> 16"     "$(mask_to_cidr 255.255.0.0)" "16"
eq "255.255.255.128 -> 25" "$(mask_to_cidr 255.255.255.128)" "25"
eq "非法掩码输出空"        "$(mask_to_cidr 255.1.1.1)" ""

cat > "$TMP/a.conf" <<'EOF'
port 1194
server 10.8.44.0 255.255.255.0
EOF
cat > "$TMP/b.conf" <<'EOF'
server 10.9.0.0 255.255.0.0
EOF
eq "server_cidr 读 /24" "$(server_cidr "$TMP/a.conf")" "10.8.44.0/24"
eq "server_cidr 读 /16" "$(server_cidr "$TMP/b.conf")" "10.9.0.0/16"

printf '\n\033[1;34m== 托管块识别 ==\033[0m\n'
cat > "$TMP/cur.conf" <<EOF
# 这是别人脚本生成的配置
dev tun
proto tcp
port 1194
server 10.8.44.0 255.255.255.0
user nobody
log-append /var/log/openvpn/openvpn.log
crl-verify /etc/openvpn/server/crl.pem
push "dhcp-option DNS 1.1.1.1"
${MARK_BEGIN}
port 1194
proto udp
push "dhcp-option DNS 8.8.8.8"
${MARK_END}
verb 3
EOF
eq "托管块指令名被识别（去重排序）" "$(managed_directives "$TMP/cur.conf" | tr '\n' ' ')" "port proto push "
check "非托管行里不含托管块内容" not_contains 'dhcp-option DNS 8\.8\.8\.8' "$(nonmanaged_lines "$TMP/cur.conf")"
check "非托管行里不含注释"       not_contains '^#' "$(nonmanaged_lines "$TMP/cur.conf")"
check "非托管行保留了别人的原有指令" contains 'proto tcp' "$(nonmanaged_lines "$TMP/cur.conf")"
eq "托管块内的 push 行被取出" "$(managed_push_lines "$TMP/cur.conf" | tr -d '\n')" 'push "dhcp-option DNS 8.8.8.8"'

printf '\n\033[1;34m== push 白名单 ==\033[0m\n'
check "允许 dhcp-option DNS"        validate_push '"dhcp-option DNS 8.8.8.8"'
check "允许 route 网络+掩码"        validate_push '"route 192.168.1.0 255.255.255.0"'
check "允许 route 单地址"           validate_push '"route 10.0.0.5"'
check "允许 redirect-gateway def1"  validate_push '"redirect-gateway def1 bypass-dhcp"'
check "允许 sndbuf 数字"            validate_push '"sndbuf 393216"'
check "拒绝 dhcp-option DOMAIN"     not validate_push '"dhcp-option DOMAIN evil.example"'
check "拒绝 dhcp-option DNS 非法值" not validate_push '"dhcp-option DNS not-an-ip"'
check "拒绝 script-security"        not validate_push '"script-security 2"'
check "拒绝 up 指令"                not validate_push '"up /tmp/evil.sh"'
check "拒绝 plugin"                 not validate_push '"plugin /tmp/evil.so"'
check "拒绝 route 里的注入"         not validate_push '"route 1.2.3.4; rm -rf /"'

printf '\n\033[1;34m== 托管块整体校验 ==\033[0m\n'
block_reason=""
check "合法托管块通过" validate_managed_block "$TMP/cur.conf"

cat > "$TMP/bad1.conf" <<EOF
${MARK_BEGIN}
script-security 2
${MARK_END}
EOF
block_reason="$(validate_managed_block "$TMP/bad1.conf")" && rc=0 || rc=$?
eq "托管块里的 script-security 被拒" "$rc" "1"
contains '未授权指令' "$block_reason"

cat > "$TMP/bad2.conf" <<EOF
${MARK_BEGIN}
push "up /bin/sh"
${MARK_END}
EOF
block_reason="$(validate_managed_block "$TMP/bad2.conf")" && rc=0 || rc=$?
eq "托管块里的恶意 push 被拒" "$rc" "1"
contains 'push 内容不被允许' "$block_reason"

printf '\n\033[1;34m== 核心安全属性：候选不得引入新的非托管指令 ==\033[0m\n'
# 合法候选：非托管部分与当前配置完全一致，只改了托管块
cat > "$TMP/good.conf" <<EOF
# 这是别人脚本生成的配置
dev tun
# proto tcp（已被面板接管）
# port 1194（已被面板接管）
server 10.8.44.0 255.255.255.0
user nobody
log-append /var/log/openvpn/openvpn.log
crl-verify /etc/openvpn/server/crl.pem
# push "dhcp-option DNS 1.1.1.1"（已被面板接管）
${MARK_BEGIN}
port 443
proto udp
push "dhcp-option DNS 8.8.8.8"
${MARK_END}
verb 3
EOF
eq "合法候选：无越权新增" "$(unauthorized_extra "$TMP/cur.conf" "$TMP/good.conf")" ""

# 恶意候选 1：托管块外加了 script-security
cat > "$TMP/evil1.conf" <<EOF
dev tun
server 10.8.44.0 255.255.255.0
script-security 2
user nobody
log-append /var/log/openvpn/openvpn.log
crl-verify /etc/openvpn/server/crl.pem
${MARK_BEGIN}
port 1194
${MARK_END}
verb 3
EOF
extra="$(unauthorized_extra "$TMP/cur.conf" "$TMP/evil1.conf")"
check "恶意候选 1：托管块外的 script-security 被检出" contains 'script-security' "$extra"

# 恶意候选 2：托管块外加了 up（可执行任意命令）
cat > "$TMP/evil2.conf" <<EOF
dev tun
server 10.8.44.0 255.255.255.0
up /tmp/evil.sh
user nobody
log-append /var/log/openvpn/openvpn.log
crl-verify /etc/openvpn/server/crl.pem
${MARK_BEGIN}
port 1194
${MARK_END}
verb 3
EOF
extra="$(unauthorized_extra "$TMP/cur.conf" "$TMP/evil2.conf")"
check "恶意候选 2：托管块外的 up 被检出" contains 'up /tmp/evil\.sh' "$extra"

# 恶意候选 3：偷偷改掉 crl-verify 指向（等于关掉吊销）
cat > "$TMP/evil3.conf" <<EOF
dev tun
server 10.8.44.0 255.255.255.0
user nobody
log-append /var/log/openvpn/openvpn.log
crl-verify /dev/null
${MARK_BEGIN}
port 1194
${MARK_END}
verb 3
EOF
extra="$(unauthorized_extra "$TMP/cur.conf" "$TMP/evil3.conf")"
check "恶意候选 3：偷偷把 crl-verify 改成 /dev/null 被检出" contains 'crl-verify /dev/null' "$extra"

# 恶意候选 4：把 crl-verify 整行**删掉**（等于关掉证书吊销）。
# 单向的 comm -13 只能发现"新增"，所以必须另有反向检查。
cat > "$TMP/evil4.conf" <<EOF
dev tun
server 10.8.44.0 255.255.255.0
user nobody
log-append /var/log/openvpn/openvpn.log
${MARK_BEGIN}
port 1194
${MARK_END}
verb 3
EOF
lost="$(removed_lines "$TMP/cur.conf" "$TMP/evil4.conf")"
check "恶意候选 4：删掉 crl-verify 被检出为『移除』" contains 'crl-verify /etc/openvpn/server/crl\.pem' "$lost"
check "删除不会被误判为已退役" not retired_as_comment "$TMP/evil4.conf" 'crl-verify /etc/openvpn/server/crl.pem'

# 合法做法：同一行被注释成退役行保留下来
cat > "$TMP/retired.conf" <<EOF
dev tun
server 10.8.44.0 255.255.255.0
user nobody
log-append /var/log/openvpn/openvpn.log
${RETIRED_PREFIX}crl-verify /etc/openvpn/server/crl.pem
${MARK_BEGIN}
port 1194
${MARK_END}
verb 3
EOF
check "注释退役的行被认可（不算删除）" retired_as_comment "$TMP/retired.conf" 'crl-verify /etc/openvpn/server/crl.pem'

printf '\n\033[1;34m== 安装脚本生成的托管块必须能通过助手校验 ==\033[0m\n'
# 把 install.sh 里那段 heredoc 的托管块抽出来、代入示例值，再交给助手的校验函数。
# 这条能拦住一种致命组合：安装脚本往托管块里放了助手不认的指令
# ——那样每一台新装的机器，在面板里保存设置都会被拒绝。
sample_subs() {
  sed -e 's/\${VPN_PORT}/1194/g' \
      -e 's/\${VPN_PROTO}/udp/g' \
      -e 's/\${VPN_NETWORK}/10.8.44.0/g' \
      -e 's/\${VPN_NETMASK}/255.255.255.0/g' \
      -e 's/\${dns_lines}/push "dhcp-option DNS 8.8.8.8"/g' \
      -e 's/\${push_routes}/push "redirect-gateway def1 bypass-dhcp"/g' \
      -e 's/\${ipv6_lines}//g' \
      -e 's/\${exit_notify}/explicit-exit-notify 1/g' \
      -e 's/\${fast_io}/fast-io/g'
}
awk -v b="$MARK_BEGIN" -v e="$MARK_END" '$0==b{f=1} f{print} $0==e{f=0}' \
  "${ROOT}/install.sh" | sample_subs > "$TMP/gen.txt"

if [ -s "$TMP/gen.txt" ]; then
  ok "从 install.sh 抽到托管块（$(grep -c . "$TMP/gen.txt") 行）"
  reason=""
  if reason="$(validate_managed_block "$TMP/gen.txt")"; then
    ok "安装脚本生成的托管块通过助手的指令/push 白名单"
  else
    bad "安装脚本生成的托管块被助手拒绝：$reason"
  fi
  # 逐条确认关键指令确实在块里（漏了就等于面板改不了）
  for d in port proto server topology mssfix sndbuf rcvbuf keepalive client-to-client; do
    check "托管块含 ${d}" grep -qE "^${d}([[:space:]]|$)" "$TMP/gen.txt"
  done
  check "托管块含 push" contains '^push ' "$(cat "$TMP/gen.txt")"
  # 负向对照：往块里塞一个助手不认的指令，必须被拒
  { cat "$TMP/gen.txt"; echo "txqueuelen 1000"; } > "$TMP/gen-bad.txt"
  # 注意 txqueuelen 必须插在结束标记之前才算在块内
  awk -v e="$MARK_END" '$0==e{print "txqueuelen 1000"} {print}' "$TMP/gen.txt" > "$TMP/gen-bad.txt"
  if validate_managed_block "$TMP/gen-bad.txt" >/dev/null 2>&1; then
    bad "托管块里混入 txqueuelen 竟然通过了校验（白名单失效）"
  else
    ok "托管块里混入未授权指令会被拒（负向对照）"
  fi
else
  bad "没能从 install.sh 抽取托管块（标记或 heredoc 结构变了？）"
fi

printf '\n\033[1;34m== 跨组件契约：面板与助手必须一致 ==\033[0m\n'
# 标记文本、托管指令集合、push 前缀三组常量在 Python 与 Bash 两边各写了一份，
# 任一边改了另一边没改都会导致"面板每次保存都被助手拒绝"。交给专用脚本逐项比对。
if [ -f "${ROOT}/backend/app/services/server_conf_writer.py" ] && command -v python3 >/dev/null 2>&1; then
  if python3 "${ROOT}/tests/check_apply_contract.py" > "$TMP/contract.txt" 2>&1; then
    ok "标记 / 托管指令集合 / push 前缀 三项契约一致"
  else
    bad "跨组件契约不一致（运行 python3 tests/check_apply_contract.py 看详情）"
    sed 's/^/      /' "$TMP/contract.txt"
  fi
else
  skip "面板端尚未实现或缺少 python3，跳过契约检查"
fi

printf '\n\033[1;34m== 脚本自身可被 bash 解析 ==\033[0m\n'
for f in templates/apply-config.sh templates/apply-validate.sh; do
  if bash -n "${ROOT}/${f}" 2>/dev/null; then ok "语法 OK：${f}"; else bad "语法错误：${f}"; fi
done

printf '\n\033[1;34m== 就绪判定（防止退回"看到一次 active 就算成功"）\033[0m\n'
HELPER="${ROOT}/templates/apply-config.sh"
# 背景：openvpn 的 `user <不存在的人>` 这类错误发生在降权之后，进程会先短暂
# active 再退出。旧逻辑"看到一次 active 就 break"会判成功，把坏配置留在盘上——
# 实测导致生产 VPN 中断，而面板显示"已应用"。现在要求连续保持 active 若干秒。
check "就绪判定使用连续阈值常量" \
  grep -q 'STABLE_ACTIVE_SECONDS' "$HELPER"
check "存在 stable 计数器自增" \
  grep -q 'stable=\$((stable + 1))' "$HELPER"
check "遇到 failed 立即中止等待" \
  grep -qE '^[[:space:]]*failed\)' "$HELPER"
check "未达到连续阈值会调用 do_rollback" \
  grep -q 'do_rollback' "$HELPER"
check "没有退回『一次 active 就 break』的老写法" \
  not grep -qE '\$state.*=.*"active".*&&.*break' "$HELPER"
printf '\n\033[1;34m==== 结果：通过 %d，失败 %d ====\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
