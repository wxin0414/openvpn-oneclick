#!/usr/bin/env bash
#
# install.sh 纯逻辑功能测试（不需要 Linux 服务器，Git Bash 也能跑）
#
# 覆盖最容易出错、且往往只在真机上才暴露的部分：
#   * cidr_to_mask 掩码换算
#   * write_client_ovpn：客户端配置必须与服务端 server.conf 保持一致
#     （proto / data-ciphers / tls-crypt 与 tls-auth / explicit-exit-notify）
#   * build_push_routes：全局代理与分流两种模式
#   * resolve_server_addr：追加客户端时能从已有配置继承服务器地址
#   * list_clients 的输出与吊销标记
#
# 用法：bash tests/test_functions.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 先加载 install.sh（只定义函数，不执行 main）。注意它自己也定义了 ok/info/warn/die，
# 会覆盖同名函数，所以本测试的助手函数必须在 source **之后**定义。
# 同时清空位置参数，避免把测试脚本的参数喂给 install.sh 的参数解析。
set --
# shellcheck disable=SC1090
source "${ROOT}/install.sh" >/dev/null 2>&1
set +e +u
trap - ERR      # 关掉 install.sh 装的 trap，失败统计交给本测试

command -v write_client_ovpn >/dev/null 2>&1 || { echo "无法从 install.sh 加载函数"; exit 1; }

pass=0; fail=0
ok()  { printf '  \033[1;32m[PASS]\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*"; fail=$((fail+1)); }
skip(){ printf '  \033[1;33m[SKIP]\033[0m %s\n' "$*"; }

# 断言：check <描述> <命令...>
check() { local desc="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }
# 断言：字符串相等
eq() { if [ "$2" = "$3" ]; then ok "$1（=$3）"; else bad "$1：实际 '$2'，期望 '$3'"; fi; }
# 断言：第二个字符串包含 / 不包含（第一个参数是扩展正则）
contains()     { printf '%s' "$2" | grep -qE "$1"; }
not_contains() { ! printf '%s' "$2" | grep -qE "$1"; }
# 断言取反
not() { ! "$@"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---- 假的服务端环境 ----
SERVER_DIR="$TMP/server"
EASYRSA_DIR="$TMP/easy-rsa"
CLIENT_EXPORT_DIR="$TMP/client"
LOG_DIR="$TMP/log"
CRL_PATH="$SERVER_DIR/crl.pem"
TA_KEY="$SERVER_DIR/ta.key"
SERVER_CONF="$TMP/server.conf"
SERVER_CN="server"
MGMT_PORT="7505"
SERVER_ADDR="203.0.113.7"

mkdir -p "$SERVER_DIR" "$EASYRSA_DIR/pki/issued" "$EASYRSA_DIR/pki/private" "$CLIENT_EXPORT_DIR" "$LOG_DIR"
echo "FAKE-CA"    > "$SERVER_DIR/ca.crt"
echo "FAKE-TAKEY" > "$TA_KEY"
echo "FAKE-CRL"   > "$CRL_PATH"
echo "FAKE-CERT"  > "$EASYRSA_DIR/pki/issued/demo.crt"
echo "FAKE-KEY"   > "$EASYRSA_DIR/pki/private/demo.key"

write_conf() { # write_conf <proto> <crypt|auth>
  cat > "$SERVER_CONF" <<EOF
port 1194
proto $1
dev tun
tls-$2 $TA_KEY
data-ciphers AES-256-GCM:AES-128-GCM
auth SHA256
server 10.8.44.0 255.255.255.0
client-config-dir $TMP/ccd
crl-verify $CRL_PATH
management 127.0.0.1 $MGMT_PORT
status $LOG_DIR/openvpn-status.log
status-version 3
EOF
}

printf '\n\033[1;34m== cidr_to_mask ==\033[0m\n'
eq "cidr /24" "$(cidr_to_mask 24)" "255.255.255.0"
eq "cidr /16" "$(cidr_to_mask 16)" "255.255.0.0"
eq "cidr /8"  "$(cidr_to_mask 8)"  "255.0.0.0"
eq "cidr /25" "$(cidr_to_mask 25)" "255.255.255.128"

printf '\n\033[1;34m== write_client_ovpn（UDP + tls-crypt）==\033[0m\n'
write_conf udp crypt
out="$(write_client_ovpn demo)"
body="$(cat "$out" 2>/dev/null)"
check "生成了 .ovpn 文件" test -s "$out"
check "含 proto udp"                  contains '^proto udp$' "$body"
check "remote 使用传入的地址与端口"     contains '^remote 203\.0\.113\.7 1194$' "$body"
check "UDP 下含 explicit-exit-notify" contains '^explicit-exit-notify 1$' "$body"
check "data-ciphers 跟随服务端"        contains '^data-ciphers AES-256-GCM:AES-128-GCM$' "$body"
check "含 tls-crypt 块"               contains '<tls-crypt>' "$body"
check "不含 tls-auth 块"              not_contains '<tls-auth>' "$body"
check "含内联 ca"                     contains '<ca>' "$body"
check "含内联 cert"                   contains '<cert>' "$body"
check "含内联 key"                    contains '<key>' "$body"
# Git Bash（MINGW/MSYS）不保留 chmod 位，权限断言只在 Linux 上有意义
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) skip "Git Bash 不保留 chmod 位，跳过 0600 权限断言" ;;
  *) eq "配置权限 0600" "$(stat -c '%a' "$out" 2>/dev/null)" "600" ;;
esac

printf '\n\033[1;34m== write_client_ovpn（TCP + tls-auth）==\033[0m\n'
write_conf tcp auth
out2="$(write_client_ovpn demo)"
body2="$(cat "$out2" 2>/dev/null)"
check "含 proto tcp"                     contains '^proto tcp$' "$body2"
check "TCP 下不含 explicit-exit-notify"   not_contains 'explicit-exit-notify' "$body2"
check "改用 tls-auth 块"                 contains '<tls-auth>' "$body2"
check "tls-auth 模式带 key-direction"    contains '^key-direction 1$' "$body2"
check "不含 tls-crypt 块"                not_contains '<tls-crypt>' "$body2"

printf '\n\033[1;34m== build_push_routes ==\033[0m\n'
FULL_TUNNEL=1; EXTRA_ROUTES=()
eq "全局代理推送 redirect-gateway" "$(build_push_routes)" 'push "redirect-gateway def1 bypass-dhcp"'
FULL_TUNNEL=0; EXTRA_ROUTES=("192.168.1.0/24" "10.10.0.0 255.255.0.0")
routes="$(build_push_routes)"
check "分流：CIDR 转成 network+掩码"       contains 'push "route 192\.168\.1\.0 255\.255\.255\.0"' "$routes"
check "分流：已是 network+掩码 时原样保留"  contains 'push "route 10\.10\.0\.0 255\.255\.0\.0"' "$routes"
check "分流：不推送 redirect-gateway"      not_contains 'redirect-gateway' "$routes"

printf '\n\033[1;34m== resolve_server_addr（追加客户端时）==\033[0m\n'
SERVER_ADDR=""
resolve_server_addr
eq "从已有 .ovpn 继承服务器地址" "$SERVER_ADDR" "203.0.113.7"
SERVER_ADDR="198.51.100.9"
resolve_server_addr
eq "已显式指定时不覆盖" "$SERVER_ADDR" "198.51.100.9"

printf '\n\033[1;34m== list_clients ==\033[0m\n'
SERVER_ADDR="203.0.113.7"
printf 'V\t300101000000Z\t\t01\tunknown\t/CN=demo\n' > "$EASYRSA_DIR/pki/index.txt"
listing="$(list_clients)"
check "列出已有客户端"        contains 'demo' "$listing"
check "未吊销时不显示标记"     not_contains '已吊销' "$listing"
printf 'R\t300101000000Z\t250101000000Z\t01\tunknown\t/CN=demo\n' > "$EASYRSA_DIR/pki/index.txt"
listing2="$(list_clients)"
check "已吊销时显示标记"      contains '已吊销' "$listing2"

printf '\n\033[1;34m== merge_env：升级只补缺失键、绝不改既有值 ==\033[0m\n'
tpl="$TMP/new.env"; dst="$TMP/existing.env"
cat > "$tpl" <<'EOF'
# 新版本模板
APP_NAME=New Name
SECRET_KEY=__GENERATED__
NEW_KEY_1=v1
NEW_KEY_2=v2
EOF
cat > "$dst" <<'EOF'
# 用户自己的配置
APP_NAME=用户改过的名字
SECRET_KEY=keep-this-secret
EOF
merge_env "$tpl" "$dst" >/dev/null 2>&1
eq "既有 APP_NAME 未被覆盖"   "$(grep -c '^APP_NAME=用户改过的名字$' "$dst")" "1"
eq "既有 SECRET_KEY 未被覆盖" "$(grep -c '^SECRET_KEY=keep-this-secret$' "$dst")" "1"
eq "新键 NEW_KEY_1 已补入"     "$(grep -c '^NEW_KEY_1=v1$' "$dst")" "1"
eq "新键 NEW_KEY_2 已补入"     "$(grep -c '^NEW_KEY_2=v2$' "$dst")" "1"
eq "APP_NAME 没有出现两次"     "$(grep -c '^APP_NAME=' "$dst")" "1"
eq "SECRET_KEY 没有出现两次"   "$(grep -c '^SECRET_KEY=' "$dst")" "1"
merge_env "$tpl" "$dst" >/dev/null 2>&1
eq "重复执行是幂等的"         "$(grep -c '^NEW_KEY_1=' "$dst")" "1"

printf '\n\033[1;34m== version_gt ==\033[0m\n'
check "1.1.0 > 1.0.0"                  version_gt 1.1.0 1.0.0
check "1.10.0 > 1.9.0（按版本非字典序）" version_gt 1.10.0 1.9.0
check "相同版本不算有新版本"            not version_gt 1.1.0 1.1.0
check "旧版本不算有新版本"              not version_gt 1.0.0 1.1.0

printf '\n\033[1;34m== mask_to_cidr ==\033[0m\n'
eq "255.255.255.0 -> 24"   "$(mask_to_cidr 255.255.255.0)" "24"
eq "255.255.0.0 -> 16"     "$(mask_to_cidr 255.255.0.0)" "16"
eq "255.255.255.128 -> 25" "$(mask_to_cidr 255.255.255.128)" "25"
eq "0.0.0.0 -> 0"          "$(mask_to_cidr 0.0.0.0)" "0"
check "非法掩码判为失败"   not mask_to_cidr 255.1.1.1

printf '\n\033[1;34m== 既有安装探测与采纳（hwdsl2/angristan 布局）==\033[0m\n'
LEGACY="$TMP/legacy"
mkdir -p "$LEGACY/server/easy-rsa/pki/issued" "$LEGACY/server/easy-rsa/pki/private"
# 故意用与默认值(10.8.44.0/24)不同的网段，验证采纳时会去读对方的真实配置
printf 'port 1194\nproto udp\nserver 10.9.0.0 255.255.0.0\ntls-crypt %s/server/tc.key\n' "$LEGACY" > "$LEGACY/server/server.conf"
echo "LEGACY-TCKEY" > "$LEGACY/server/tc.key"
echo "LEGACY-CRL"   > "$LEGACY/server/crl.pem"
echo "LEGACY-CA"    > "$LEGACY/server/easy-rsa/pki/ca.crt"

OVPN_DIR="$LEGACY"; SERVER_DIR="$LEGACY/server"; SERVER_CN="server"
EXISTING_FOUND=0; EXISTING_TLS_KEY=""; EXISTING_TLS_MODE=""; EXISTING_PKI_DIR=""; EXISTING_SERVER_CONF=""
detect_existing_install
eq "识别出既有安装"         "$EXISTING_FOUND" "1"
eq "找到对方的 tc.key"      "$EXISTING_TLS_KEY" "$LEGACY/server/tc.key"
eq "识别为 tls-crypt"       "$EXISTING_TLS_MODE" "tls-crypt"
eq "找到对方的 PKI"         "$EXISTING_PKI_DIR" "$LEGACY/server/easy-rsa"

TA_KEY="$LEGACY/server/ta.key"; EASYRSA_DIR="$LEGACY/easy-rsa"
CRL_PATH="$LEGACY/crl.pem"; CCD_DIR="$LEGACY/ccd"
adopt_layout >/dev/null 2>&1
eq "采纳后复用 tc.key（否则既有客户端全部失效）" "$TA_KEY" "$LEGACY/server/tc.key"
eq "采纳后指向对方的 server.conf"               "$SERVER_CONF" "$LEGACY/server/server.conf"
eq "采纳后复用对方的 PKI（不新建第二套）"        "$EASYRSA_DIR" "$LEGACY/server/easy-rsa"
eq "采纳后 crl-verify 指向对方已有 CRL"         "$CRL_PATH" "$LEGACY/server/crl.pem"
eq "采纳后读到对方的 VPN 网段"                  "$VPN_NETWORK" "10.9.0.0"
eq "采纳后读到对方的掩码"                       "$VPN_NETMASK" "255.255.0.0"
eq "采纳后换算出正确的 CIDR"                    "$VPN_CIDR" "16"
eq "采纳后读到对方的协议"                       "$VPN_PROTO" "udp"
eq "采纳后读到对方的端口"                       "$VPN_PORT" "1194"

printf '\n\033[1;34m== inject_panel_directives：只补缺失、不重复 ==\033[0m\n'
SERVER_CONF="$LEGACY/server/server.conf"
LOG_DIR="$LEGACY/log"; MGMT_PORT=7505; CCD_DIR="$LEGACY/server/ccd"; CRL_PATH="$LEGACY/server/crl.pem"
inject_panel_directives >/dev/null 2>&1
inj="$(cat "$SERVER_CONF")"
check "补上 management"        contains '^management 127\.0\.0\.1 7505$' "$inj"
check "补上 status"            contains '^status ' "$inj"
check "补上 status-version 3"  contains '^status-version 3$' "$inj"
check "补上 client-config-dir" contains '^client-config-dir ' "$inj"
check "补上 crl-verify"        contains '^crl-verify ' "$inj"
check "对方原有的 tls-crypt 行保留" contains '^tls-crypt ' "$inj"
inject_panel_directives >/dev/null 2>&1
eq "重复执行不会重复追加 management" "$(grep -c '^management ' "$SERVER_CONF")" "1"
eq "重复执行不会重复追加 status-version" "$(grep -c '^status-version ' "$SERVER_CONF")" "1"

printf '\n\033[1;34m== write_server_conf：分流不推公共 DNS / IPv6 黑洞 ==\033[0m\n'
SERVER_DIR="$TMP/srv2"; SERVER_CN="server"; TA_KEY="$TMP/srv2/ta.key"; CRL_PATH="$TMP/srv2/crl.pem"
LOG_DIR="$TMP/log2"; CCD_DIR="$TMP/ccd2"; MGMT_PORT=7505; DNS1=8.8.8.8; DNS2=8.8.4.4
SERVER_CONF="$TMP/srv2/server.conf"; ADOPTED=0; VPN_PROTO=udp; VPN_PORT=1194
VPN_NETWORK=10.8.44.0; VPN_NETMASK=255.255.255.0
mkdir -p "$SERVER_DIR" "$LOG_DIR" "$CCD_DIR"

FULL_TUNNEL=0; EXTRA_ROUTES=("192.168.1.0/24"); DNS_EXPLICIT=0; BLOCK_IPV6=0
write_server_conf >/dev/null 2>&1
conf="$(cat "$SERVER_CONF")"
check "分流模式不推公共 DNS（避免内网域名被解析到公网）" not_contains 'dhcp-option DNS' "$conf"
check "分流模式推送了指定路由" contains 'push "route 192\.168\.1\.0 255\.255\.255\.0"' "$conf"
check "分流模式不推 redirect-gateway" not_contains 'redirect-gateway' "$conf"

DNS_EXPLICIT=1
write_server_conf >/dev/null 2>&1
check "显式指定 DNS 时会推送" contains 'dhcp-option DNS 8\.8\.8\.8' "$(cat "$SERVER_CONF")"

BLOCK_IPV6=1; FULL_TUNNEL=1
write_server_conf >/dev/null 2>&1
conf="$(cat "$SERVER_CONF")"
check "写入 block-ipv6"                 contains '^block-ipv6$' "$conf"
check "推送 ifconfig-ipv6"              contains 'push "ifconfig-ipv6 ' "$conf"
check "推送 redirect-gateway ipv6"      contains 'push "redirect-gateway ipv6"' "$conf"
check "全局代理仍推 redirect-gateway def1" contains 'push "redirect-gateway def1 bypass-dhcp"' "$conf"

printf '\n\033[1;34m== 模拟升级：新模板 + 既有 .env ==\033[0m\n'
# 复刻 deploy_panel 的升级路径：先把新模板渲染好（会生成全新的密钥/口令），
# 再合并进既有 .env。要求：新生成的密钥与口令一个字都不许写进去。
up_env="$TMP/upgrade.env"; up_tpl="$TMP/upgrade.tpl"; up_rendered="$TMP/upgrade.rendered"
cat > "$up_tpl" <<'EOF'
SECRET_KEY=__SECRET_KEY__
DEFAULT_ADMIN_PASSWORD=__ADMIN_PASSWORD__
APP_NAME=OpenVPN Web Admin
NEW_FEATURE_FLAG=1
EOF
cat > "$up_env" <<'EOF'
SECRET_KEY=original-secret-abc123
DEFAULT_ADMIN_PASSWORD=original-password-xyz
APP_NAME=我的面板
EOF
render "$up_tpl" "$up_rendered" SECRET_KEY "brand-new-generated-key" ADMIN_PASSWORD "brand-new-password"
merge_env "$up_rendered" "$up_env" >/dev/null 2>&1
eq "升级后 SECRET_KEY 仍是原值"     "$(grep -c '^SECRET_KEY=original-secret-abc123$' "$up_env")" "1"
eq "升级后管理员口令仍是原值"       "$(grep -c '^DEFAULT_ADMIN_PASSWORD=original-password-xyz$' "$up_env")" "1"
eq "新生成的密钥没有被写进去"       "$(grep -c 'brand-new-generated-key' "$up_env")" "0"
eq "新生成的口令没有被写进去"       "$(grep -c 'brand-new-password' "$up_env")" "0"
eq "用户改过的 APP_NAME 保留"       "$(grep -c '^APP_NAME=我的面板$' "$up_env")" "1"
eq "新版本新增的开关已补入"         "$(grep -c '^NEW_FEATURE_FLAG=1$' "$up_env")" "1"

printf '\n\033[1;34m== 模板占位符一致性 ==\033[0m\n'
if command -v python3 >/dev/null 2>&1; then
  if python3 "${ROOT}/tests/check_templates.py" >/dev/null 2>&1; then
    ok "install.sh 能完整渲染所有模板"
  else
    bad "install.sh 未能完整渲染模板（详见 python3 tests/check_templates.py）"
  fi
else
  printf '  [SKIP] 无 python3，跳过\n'
fi

printf '\n\033[1;34m== 布局探测（防止重跑把正常 VPN 弄坏）\033[0m\n'
# 背景：Debian 12 的 openvpn 包同时提供 openvpn@.service 与 openvpn-server@.service。
# 旧逻辑只看"哪个 unit 存在"，于是重跑安装会选到与既有配置不同的布局：写出竞争
# 配置、服务读不到它期望的路径而启动失败、两套 unit 都 enable 后互相抢 1194。
# 现在改为"以已有配置文件为准"，下面把三种情形钉住。
_layout_probe() { # $1=classic 存在 $2=serverdir 存在 $3=unit 存在
  local tmp; tmp="$(mktemp -d)" || return 1
  mkdir -p "$tmp/classic" "$tmp/serverdir" "$tmp/bin"
  [ "$1" = 1 ] && : > "$tmp/classic/server.conf"
  [ "$2" = 1 ] && : > "$tmp/serverdir/server.conf"
  if [ "$3" = 1 ]; then
    printf '#!/bin/sh\necho "openvpn-server@.service enabled"\n' > "$tmp/bin/systemctl"
  else
    printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/systemctl"
  fi
  chmod +x "$tmp/bin/systemctl"
  OVPN_DIR="$tmp/classic"; SERVER_DIR="$tmp/serverdir"; SERVER_CN=server
  OVPN_SERVICE=""; SERVER_CONF=""
  PATH="$tmp/bin:$PATH" resolve_openvpn_layout >/dev/null 2>&1
  local which=other
  case "$SERVER_CONF" in
    "$tmp/classic"/*)   which=classic ;;
    "$tmp/serverdir"/*) which=serverdir ;;
  esac
  printf '%s|%s' "$OVPN_SERVICE" "$which"
  rm -rf "$tmp"
}

r="$(_layout_probe 1 0 1)"
check "只有 /etc/openvpn/server.conf 时选 openvpn@server（即使 unit 存在）" \
  test "$r" = "openvpn@server|classic"
r="$(_layout_probe 0 1 1)"
check "只有 /etc/openvpn/server/server.conf 时选 openvpn-server@server" \
  test "$r" = "openvpn-server@server|serverdir"
r="$(_layout_probe 1 1 1)"
check "两套配置并存时沿用 classic 且不切换布局" \
  test "$r" = "openvpn@server|classic"
r="$(_layout_probe 0 0 1)"
check "两者都不存在且 unit 存在时用 openvpn-server@server" \
  test "$r" = "openvpn-server@server|serverdir"
r="$(_layout_probe 0 0 0)"
check "两者都不存在且无 server unit 时用 openvpn@server" \
  test "$r" = "openvpn@server|classic"

# adopt_layout 必须由**配置路径**推导服务名（只看 unit 会把既有配置配给读另一个
# 路径的服务 —— 这正是那次事故的直接原因）
check "adopt_layout 里服务名由 SERVER_CONF 路径决定" \
  grep -q 'case "$SERVER_CONF" in' "${ROOT}/install.sh"
# adopt_layout 里若又出现 list-unit-files，说明有人把"按 unit 挑服务名"的老逻辑
# 加回来了 —— 那正是那次事故的直接原因（配置在 /etc/openvpn/server.conf 却选了
# openvpn-server@server，后者读的是 /etc/openvpn/server/server.conf，于是起不来）。
_adopt_uses_unit_check() {
  awk '/^adopt_layout\(\)/{f=1} f{print} f && /^\}/{exit}' "${ROOT}/install.sh" \
    | grep -q 'list-unit-files'
}
check "adopt_layout 不再按 unit 存在与否挑服务名" not _adopt_uses_unit_check
printf '\n\033[1;34m==== 结果：通过 %d，失败 %d ====\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
