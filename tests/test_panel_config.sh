#!/usr/bin/env bash
#
# apply-panel-config 的校验逻辑测试（不需要 root，开发机上就能跑）
#
# 覆盖两件事：
#   1. 「面板设置」写下去的值会被怎么拒绝（端口/域名/CIDR/TLS 规则）；
#   2. 两个助手共用的候选文件校验（属主/权限/大小/NUL）与 nginx 渲染结果。
#
# 助手本身要 nginx / systemctl / openssl，没法在开发机上端到端跑；但规则是纯函数，
# 可以脱离 root 逐条断言 —— 这正是这个特性敢上线的原因。
#
# 用法：bash tests/test_panel_config.sh
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
# 断言某段中文原因里出现了关键词（空原因=通过，会被判失败）
has_reason() { # has_reason <描述> <原因> <关键词>
  if [ -n "$2" ] && printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1：实际原因 '$2'"; fi
}
is_ok() { # is_ok <描述> <原因>
  if [ -z "$2" ]; then ok "$1"; else bad "$1：本该通过，实际 '$2'"; fi
}

# Git Bash（MINGW/MSYS）不保留 chmod 位、也不模拟 POSIX 属主，权限类断言跳过
IS_MSYS=0
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) IS_MSYS=1 ;;
esac

# shellcheck disable=SC1090
. "${ROOT}/templates/panel-validate.sh"
# shellcheck disable=SC1090
. "${ROOT}/templates/apply-validate.sh"
# shellcheck disable=SC1090
. "${ROOT}/templates/panel-nginx.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 监听端口 ==\033[0m\n'
is_ok  "5698 合法"                 "$(panel_port_problem 5698)"
is_ok  "1 合法"                    "$(panel_port_problem 1)"
is_ok  "65535 合法"                "$(panel_port_problem 65535)"
has_reason "0 被拒"                "$(panel_port_problem 0)" "1-65535"
has_reason "65536 被拒"            "$(panel_port_problem 65536)" "1-65535"
has_reason "负数被拒"              "$(panel_port_problem -1)" "整数"
has_reason "非数字被拒"            "$(panel_port_problem abc)" "整数"
has_reason "空值被拒"              "$(panel_port_problem "")" "整数"
has_reason "前导零被拒"            "$(panel_port_problem 05698)" "前导零"
has_reason "与 OpenVPN 端口相同被拒" "$(panel_port_problem 1194 1194)" "OpenVPN 端口"
is_ok      "与其它端口不冲突时放行"  "$(panel_port_problem 1195 1194)"
is_ok      "未提供 OpenVPN 端口时不做冲突判定" "$(panel_port_problem 1194)"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 绑定域名 ==\033[0m\n'
is_ok  "_（通配）合法"          "$(panel_server_name_problem _)"
is_ok  "example.com 合法"       "$(panel_server_name_problem example.com)"
is_ok  "localhost 合法"         "$(panel_server_name_problem localhost)"
is_ok  "多级域名合法"           "$(panel_server_name_problem panel.example.com)"
is_ok  "带连字符合法"           "$(panel_server_name_problem my-panel.example.com)"
is_ok  "通配域名合法"           "$(panel_server_name_problem '*.example.com')"
is_ok  "IPv4 合法"              "$(panel_server_name_problem 203.0.113.7)"
is_ok  "IPv6 合法"              "$(panel_server_name_problem 'fd00::1')"
has_reason "空值被拒"           "$(panel_server_name_problem "")" "不能为空"
has_reason "超长被拒"           "$(panel_server_name_problem "$(printf 'a%.0s' $(seq 1 254)).com")" "过长"
has_reason "分号注入被拒"       "$(panel_server_name_problem 'evil.com; rm -rf /')" "非法字符"
has_reason "空格被拒"           "$(panel_server_name_problem 'evil com')" "非法字符"
has_reason "斜杠被拒"           "$(panel_server_name_problem 'evil/../x')" "非法字符"
has_reason "花括号被拒"         "$(panel_server_name_problem 'evil{X}')" "非法字符"
has_reason "反引号被拒"         "$(panel_server_name_problem 'evil`id`')" "非法字符"
has_reason "\$ 被拒"            "$(panel_server_name_problem 'evil$HOME')" "非法字符"
has_reason "连字符开头被拒"     "$(panel_server_name_problem '-evil.com')" "格式不正确"
has_reason "空标签被拒"         "$(panel_server_name_problem 'a..b')" "空标签"
has_reason "裸通配符被拒"       "$(panel_server_name_problem '*')" "通配符"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 放行来源与 CIDR ==\033[0m\n'
is_ok  "IPv4 地址合法"            "$(panel_cidr_problem 203.0.113.7)"
is_ok  "IPv4 /24 合法"            "$(panel_cidr_problem 10.8.44.0/24)"
is_ok  "IPv4 /32 合法"            "$(panel_cidr_problem 10.8.44.7/32)"
is_ok  "IPv4 /0 合法"             "$(panel_cidr_problem 0.0.0.0/0)"
is_ok  "IPv6 /8 合法"             "$(panel_cidr_problem fd00::/8)"
is_ok  "IPv6 地址合法"            "$(panel_cidr_problem '::1')"
has_reason "IPv4 越界被拒"        "$(panel_cidr_problem 10.0.0.256/24)" "IPv4 地址格式"
has_reason "前缀 >32 被拒"        "$(panel_cidr_problem 10.0.0.0/33)" "0-32"
has_reason "前缀非数字被拒"       "$(panel_cidr_problem 10.0.0.0/x)" "前缀长度"
has_reason "IPv6 前缀 >128 被拒"  "$(panel_cidr_problem 'fd00::/129')" "0-128"
has_reason "缺段被拒"             "$(panel_cidr_problem 10.0.0/24)" "IPv4 地址格式"
has_reason "含空白被拒"           "$(panel_cidr_problem '10.0.0.0/24 ;')" "空白"
has_reason "空条目被拒"           "$(panel_cidr_problem '')" "空条目"

is_ok  "public 合法"              "$(panel_allow_problem public "")"
is_ok  "vpn 合法"                 "$(panel_allow_problem vpn "")"
is_ok  "custom 单条合法"          "$(panel_allow_problem custom '203.0.113.0/24')"
is_ok  "custom 多条合法"          "$(panel_allow_problem custom '203.0.113.0/24,10.0.0.0/8,fd00::/8')"
is_ok  "custom 逗号后带空格也合法" "$(panel_allow_problem custom '203.0.113.0/24, 10.0.0.0/8')"
is_ok  "custom 恰好 32 条合法"    "$(panel_allow_problem custom "$(seq 1 32 | sed 's|^|10.0.|; s|$|.0/24|' | tr '\n' ',' | sed 's|,$||')")"
has_reason "custom 33 条被拒"     "$(panel_allow_problem custom "$(seq 1 33 | sed 's|^|10.0.|; s|$|.0/24|' | tr '\n' ',' | sed 's|,$||')")" "最多 32 条"
has_reason "custom 空列表被拒"    "$(panel_allow_problem custom '')" "至少要给一条"
has_reason "custom 里有一条非法就整体拒绝" "$(panel_allow_problem custom '10.0.0.0/8,10.0.0.0/33')" "0-32"
has_reason "未知模式被拒"         "$(panel_allow_problem bogus '')" "只能是"
has_reason "public 不允许带列表"  "$(panel_allow_problem public '10.0.0.0/8')" "不应给出"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== TLS 三模式 ==\033[0m\n'
is_ok  "off 合法"                 "$(panel_tls_problem off '' '')"
is_ok  "selfsigned 合法"          "$(panel_tls_problem selfsigned '' '')"
has_reason "off 不接受自定义路径"  "$(panel_tls_problem off /x.crt /x.key)" "不接受自定义证书路径"
has_reason "selfsigned 不接受自定义路径" "$(panel_tls_problem selfsigned /x.crt /x.key)" "不接受自定义证书路径"
has_reason "未知模式被拒"         "$(panel_tls_problem bogus '' '')" "只能是"
has_reason "existing 缺路径被拒"  "$(panel_tls_problem existing '' '')" "必须同时给出"
has_reason "existing 不存在被拒"  "$(panel_tls_problem existing /nope/a.crt /nope/a.key)" "不存在"
has_reason "existing 相对路径被拒" "$(panel_tls_problem existing ./a.crt ./a.key)" "绝对路径"

if command -v openssl >/dev/null 2>&1; then
  CERT_DIR="$TMP/tls"
  mkdir -p "$CERT_DIR"
  # Git Bash 会把 -subj 里的 /CN=... 当成路径改写，用 //CN= 的等价写法绕开
  SUBJ_PREFIX="/"
  [ "$IS_MSYS" = "1" ] && SUBJ_PREFIX="//"
  openssl req -x509 -newkey rsa:2048 -days 30 -nodes -sha256 \
    -subj "${SUBJ_PREFIX}CN=panel.example.com" \
    -keyout "$CERT_DIR/a.key" -out "$CERT_DIR/a.crt" >/dev/null 2>&1
  openssl req -x509 -newkey rsa:2048 -days 30 -nodes -sha256 \
    -subj "${SUBJ_PREFIX}CN=other.example.com" \
    -keyout "$CERT_DIR/b.key" -out "$CERT_DIR/b.crt" >/dev/null 2>&1
  chmod 600 "$CERT_DIR/a.key" "$CERT_DIR/b.key" 2>/dev/null || true

  if [ -s "$CERT_DIR/a.crt" ] && [ -s "$CERT_DIR/a.key" ]; then
    is_ok "existing 且证书与私钥匹配时放行" "$(panel_tls_problem existing "$CERT_DIR/a.crt" "$CERT_DIR/a.key")"
    has_reason "证书与私钥不匹配被拒" \
      "$(panel_tls_problem existing "$CERT_DIR/a.crt" "$CERT_DIR/b.key")" "不匹配"
    check "panel_cert_key_match：匹配返回 0" panel_cert_key_match "$CERT_DIR/a.crt" "$CERT_DIR/a.key"
    check "panel_cert_key_match：不匹配返回非 0" not panel_cert_key_match "$CERT_DIR/a.crt" "$CERT_DIR/b.key"
    printf '' > "$CERT_DIR/empty.key"
    has_reason "私钥为空被拒" "$(panel_tls_problem existing "$CERT_DIR/a.crt" "$CERT_DIR/empty.key")" "为空"
    if [ "$IS_MSYS" = "1" ]; then
      skip "Git Bash 不保留 chmod 位，跳过『私钥组/其他可写被拒』断言"
    else
      chmod 666 "$CERT_DIR/b.key"
      has_reason "私钥组/其他可写被拒" \
        "$(panel_tls_problem existing "$CERT_DIR/a.crt" "$CERT_DIR/b.key")" "可写"
      chmod 600 "$CERT_DIR/b.key"
    fi
  else
    skip "openssl 生成测试证书失败，跳过 existing 模式断言"
  fi
else
  skip "无 openssl，跳过 existing 模式的证书匹配断言"
fi

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 候选文件的文件级校验（两个助手共用）==\033[0m\n'
CAND="$TMP/candidate"
printf 'LISTEN_PORT=8443\n' > "$CAND"
chmod 600 "$CAND"
is_ok "合法候选通过" "$(candidate_file_problem "$CAND" "" 32768)"
has_reason "不存在被拒" "$(candidate_file_problem "$TMP/none" "" 32768)" "不存在"
mkdir -p "$TMP/adir"
has_reason "目录被拒" "$(candidate_file_problem "$TMP/adir" "" 32768)" "不是普通文件"
: > "$TMP/empty"
has_reason "空文件被拒" "$(candidate_file_problem "$TMP/empty" "" 32768)" "空文件"
{ printf 'A=1\n'; printf 'B=2\0\n'; } > "$TMP/nul"
has_reason "含 NUL 被拒" "$(candidate_file_problem "$TMP/nul" "" 32768)" "NUL"
head -c 40000 /dev/zero | tr '\0' 'x' > "$TMP/big"
has_reason "超过 32KB 被拒" "$(candidate_file_problem "$TMP/big" "" 32768)" "过大"
has_reason "属主不符被拒" "$(candidate_file_problem "$CAND" 'definitely-not-a-user' 32768)" "属主"
if [ "$IS_MSYS" = "1" ]; then
  skip "Git Bash 不保留 chmod 位，跳过『组/其他可写被拒』断言"
else
  chmod 666 "$CAND"
  has_reason "组/其他可写被拒" "$(candidate_file_problem "$CAND" "" 32768)" "可写"
  chmod 600 "$CAND"
fi
# apply-config 用的是同一份实现（128KB 上限），这里确认它没被改回内联代码
check "apply-config 复用 candidate_file_problem（不再内联校验）" \
  grep -qF 'candidate_file_problem "$CANDIDATE"' "${ROOT}/templates/apply-config.sh"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 候选内容（键白名单 + 取值）==\033[0m\n'
write_cand() { # write_cand <文件> <内容>
  printf '%s\n' "$2" > "$1"
  chmod 600 "$1"
}
GOOD_CAND="$TMP/good.candidate"
write_cand "$GOOD_CAND" 'LISTEN_PORT=8443
SERVER_NAME=panel.example.com
ALLOW_MODE=custom
ALLOW_CIDRS=203.0.113.0/24,127.0.0.1
TLS_MODE=off
TLS_CERT=
TLS_KEY='
is_ok "完整合法候选通过" "$(panel_candidate_problem "$GOOD_CAND" 1194)"
write_cand "$TMP/blankname.candidate" 'LISTEN_PORT=8443
SERVER_NAME=
ALLOW_MODE=vpn
TLS_MODE=off'
is_ok "允许留空的 SERVER_NAME（等价 _）" "$(panel_candidate_problem "$TMP/blankname.candidate" 1194)"

write_cand "$TMP/unknown.candidate" 'LISTEN_PORT=8443
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=off
EVIL_EXTRA=1'
has_reason "未知键被拒" "$(panel_candidate_problem "$TMP/unknown.candidate")" "未知键"

write_cand "$TMP/missing.candidate" 'LISTEN_PORT=8443
SERVER_NAME=x.example.com
ALLOW_MODE=vpn'
has_reason "缺少必需键被拒" "$(panel_candidate_problem "$TMP/missing.candidate")" "缺少必需的键"

write_cand "$TMP/dup.candidate" 'LISTEN_PORT=8443
LISTEN_PORT=8444
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=off'
has_reason "重复键被拒" "$(panel_candidate_problem "$TMP/dup.candidate")" "重复"

write_cand "$TMP/inject.candidate" 'LISTEN_PORT=8443
SERVER_NAME=evil; rm -rf /
ALLOW_MODE=vpn
TLS_MODE=off'
has_reason "值里的分号注入被拒" "$(panel_candidate_problem "$TMP/inject.candidate")" "不允许的字符"

write_cand "$TMP/backtick.candidate" 'LISTEN_PORT=8443
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=off
TLS_CERT=`id`'
has_reason "值里的反引号被拒" "$(panel_candidate_problem "$TMP/backtick.candidate")" "不允许的字符"

write_cand "$TMP/badline.candidate" 'LISTEN_PORT 8443'
has_reason "非 KEY=VALUE 行被拒" "$(panel_candidate_problem "$TMP/badline.candidate")" "KEY=VALUE"

write_cand "$TMP/badkey.candidate" 'listen_port=8443
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=off'
has_reason "小写键名被拒" "$(panel_candidate_problem "$TMP/badkey.candidate")" "键名不合法"

write_cand "$TMP/conflict.candidate" 'LISTEN_PORT=1194
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=off'
has_reason "端口与 OpenVPN 端口冲突被拒" "$(panel_candidate_problem "$TMP/conflict.candidate" 1194)" "OpenVPN 端口"

write_cand "$TMP/badtls.candidate" 'LISTEN_PORT=8443
SERVER_NAME=x.example.com
ALLOW_MODE=vpn
TLS_MODE=existing'
has_reason "existing 不给路径被拒" "$(panel_candidate_problem "$TMP/badtls.candidate")" "必须同时给出"

write_cand "$TMP/vpnok.candidate" 'LISTEN_PORT=8443
SERVER_NAME=x.example.com
ALLOW_MODE=public
TLS_MODE=selfsigned'
is_ok "public + selfsigned 通过" "$(panel_candidate_problem "$TMP/vpnok.candidate")"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== nginx 渲染（与 install.sh 共用同一实现）==\033[0m\n'
TPL="${ROOT}/templates/nginx.conf"
OUT="$TMP/nginx.conf"
VPN_CIDR="10.8.44.0/24"
WEB_ROOT="/var/www"
WEB_NAME="ovpnmanager"
PANEL_PORT="8000"

render_ok() { # render_ok <listen_port> <server_name> <allow_mode> <allow_cidrs> <tls_mode> <cert> <key>
  PANEL_LISTEN_PORT="$1" PANEL_SERVER_NAME="$2" PANEL_ALLOW_MODE="$3" PANEL_ALLOW_CIDRS="$4" \
    PANEL_TLS_MODE="$5" PANEL_TLS_CERT="$6" PANEL_TLS_KEY="$7" \
    panel_nginx_render "$TPL" "$OUT"
}

check "渲染（off + vpn）成功" render_ok 5698 _ vpn "" off "" ""
check "off 模式生成 listen <port>"        contains '^    listen 5698;$' "$(cat "$OUT")"
check "off 模式生成 IPv6 listen"          contains 'listen \[::\]:5698;' "$(cat "$OUT")"
check "off 模式不含 ssl"                  not_contains 'ssl_certificate' "$(cat "$OUT")"
check "vpn 模式放行 VPN 网段"             contains '^    allow 10\.8\.44\.0/24;$' "$(cat "$OUT")"
check "vpn 模式放行本机"                  contains '^    allow 127\.0\.0\.1;$' "$(cat "$OUT")"
check "allow 后面一定有 deny all"          contains '^    deny all;$' "$(cat "$OUT")"
check "server_name 被写入"                contains '^    server_name _;$' "$(cat "$OUT")"
check "反代地址保留"                      contains 'proxy_pass http://127\.0\.0\.1:8000;' "$(cat "$OUT")"
check "location 前缀保留"                 contains 'location /ovpnmanager/ \{' "$(cat "$OUT")"
eq "托管标记成对保留" "$(grep -c 'panel listen' "$OUT")" "2"
eq "放行标记成对保留" "$(grep -c 'panel allow' "$OUT")" "2"

check "渲染（public）成功" render_ok 5698 _ public "" off "" ""
check "public 放行 0.0.0.0/0" contains '^    allow 0\.0\.0\.0/0;$' "$(cat "$OUT")"

check "渲染（custom）成功" render_ok 8443 panel.example.com custom '203.0.113.0/24,198.51.100.0/24' off "" ""
check "custom 逐条写入"    contains '^    allow 203\.0\.113\.0/24;$' "$(cat "$OUT")"
check "custom 第二条写入"  contains '^    allow 198\.51\.100\.0/24;$' "$(cat "$OUT")"
eq "custom 只补一次 127.0.0.1" "$(grep -c '^    allow 127\.0\.0\.1;$' "$OUT")" "1"
check "绑定域名被写入"     contains '^    server_name panel\.example\.com;$' "$(cat "$OUT")"

check "渲染（selfsigned）成功" render_ok 8443 panel.example.com vpn "" selfsigned /tls/a.crt /tls/a.key
check "TLS 模式带 ssl 参数"    contains '^    listen 8443 ssl;$' "$(cat "$OUT")"
check "TLS 写入证书路径"       contains '^    ssl_certificate /tls/a\.crt;$' "$(cat "$OUT")"
check "TLS 写入私钥路径"       contains '^    ssl_certificate_key /tls/a\.key;$' "$(cat "$OUT")"
check "TLS 只开 1.2/1.3"       contains '^    ssl_protocols TLSv1\.2 TLSv1\.3;$' "$(cat "$OUT")"

check "TLS 模式缺证书路径时渲染失败" not render_ok 8443 x.example.com vpn "" selfsigned "" ""
check "未知放行模式渲染失败"          not render_ok 8443 _ bogus "" off "" ""

# 缺 listen_port：必须在**未设置**该变量的子 shell 里跑（env 无法调用 shell 函数）
render_without_port() {
  ( unset PANEL_LISTEN_PORT
    PANEL_ALLOW_MODE="vpn" PANEL_TLS_MODE="off" panel_nginx_render "$TPL" "$TMP/no-port.conf" )
}
check "缺 listen_port 渲染失败" not render_without_port
check "缺 listen_port 时不落盘" test ! -e "$TMP/no-port.conf"

# 模板被破坏（没有托管标记）时必须失败，而不是写出半截配置
BROKEN_TPL="$TMP/broken.conf"
grep -v 'panel listen' "$TPL" > "$BROKEN_TPL"
PANEL_LISTEN_PORT=5698 PANEL_ALLOW_MODE=vpn PANEL_TLS_MODE=off VPN_CIDR="$VPN_CIDR" \
  panel_nginx_render "$BROKEN_TPL" "$TMP/broken-out.conf" >/dev/null 2>&1
eq "模板缺托管标记时返回非 0" "$?" "1"
check "模板缺标记时不落盘" test ! -e "$TMP/broken-out.conf"

# 渲染要幂等：同样入参两次渲染结果逐字节一致（否则每次升级都会产生 diff）
render_ok 5698 _ vpn "" off "" ""
cp -f "$OUT" "$TMP/first.conf"
render_ok 5698 _ vpn "" off "" ""
check "重复渲染结果一致（幂等）" cmp -s "$TMP/first.conf" "$OUT"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== panel.state（安装脚本优先读它）==\033[0m\n'
STATE="$TMP/panel.state"
panel_write_state "$STATE" \
  PANEL_LISTEN_PORT 8443 \
  PANEL_SERVER_NAME panel.example.com \
  PANEL_ALLOW_MODE custom \
  PANEL_ALLOW_CIDRS '203.0.113.0/24,127.0.0.1' \
  PANEL_TLS_MODE off \
  PANEL_TLS_CERT '' \
  PANEL_TLS_KEY ''
eq "state 里的端口可读回"   "$(panel_state_value "$STATE" PANEL_LISTEN_PORT)" "8443"
eq "state 里的域名可读回"   "$(panel_state_value "$STATE" PANEL_SERVER_NAME)" "panel.example.com"
eq "state 里的模式可读回"   "$(panel_state_value "$STATE" PANEL_ALLOW_MODE)" "custom"
eq "state 里的 CIDR 可读回" "$(panel_state_value "$STATE" PANEL_ALLOW_CIDRS)" "203.0.113.0/24,127.0.0.1"
eq "state 里的 TLS 可读回"  "$(panel_state_value "$STATE" PANEL_TLS_MODE)" "off"
eq "state 缺键时返回空"     "$(panel_state_value "$STATE" PANEL_NOT_THERE)" ""
check "state 含说明注释"    contains '优先读本文件' "$(cat "$STATE")"
panel_write_state "$STATE" PANEL_LISTEN_PORT '$(id)'
has_reason "state 写入非法字符被替换成显眼标记" \
  "$(panel_state_value "$STATE" PANEL_LISTEN_PORT)" "__"
check "state 写入越权值时不落原文" not_contains '\$\(id\)' "$(cat "$STATE")"

# ---------------------------------------------------------------------------
printf '\n\033[1;34m== 与安装脚本 / sudoers 的契约 ==\033[0m\n'
SUDOERS="${ROOT}/templates/sudoers"
check "sudoers 授权 apply-panel-config" grep -qF '/usr/local/lib/ovpnmanager/apply-panel-config' "$SUDOERS"
check "sudoers 里 apply-panel-config 一行不接受参数" \
  grep -qE '^\S+ ALL=\(root\) NOPASSWD: /usr/local/lib/ovpnmanager/apply-panel-config$' "$SUDOERS"
check "sudoers 仍保留 apply-config 的授权" grep -qF '/usr/local/lib/ovpnmanager/apply-config' "$SUDOERS"

PANEL_CONF_TPL="${ROOT}/templates/panel.conf.template"
for key in NGINX_CONF STATE_DIR CANDIDATE PANEL_USER WEB_ROOT WEB_NAME BACKUP_DIR TLS_DIR WAIT_RELOAD_SECONDS; do
  check "panel.conf.template 含键 ${key}" grep -qE "^${key}=\"__${key}__\"" "$PANEL_CONF_TPL"
  check "install.sh 渲染了 ${key}" grep -qE "[[:space:]]${key}[[:space:]]" "${ROOT}/install.sh"
done

check "apply-panel-config 拒绝任何参数（脚本内有 \$# 检查）" \
  grep -qF 'if [ "$#" -ne 0 ]; then' "${ROOT}/templates/apply-panel-config.sh"
check "apply-panel-config 必须先 nginx -t 再 reload" \
  bash -c 'a=$(grep -n "nginx -t" "'"${ROOT}"'/templates/apply-panel-config.sh" | head -1 | cut -d: -f1)
           b=$(grep -n "systemctl reload" "'"${ROOT}"'/templates/apply-panel-config.sh" | head -1 | cut -d: -f1)
           [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]'
check "失败路径会回滚（do_rollback 存在且被失败分支调用）" \
  bash -c 'grep -q "do_rollback" "'"${ROOT}"'/templates/apply-panel-config.sh" &&
           [ "$(grep -c "do_rollback" "'"${ROOT}"'/templates/apply-panel-config.sh")" -ge 4 ]'
check "助手不打印私钥内容（无 cat/key 输出）" \
  not grep -qE 'cat "\$\{?TLS_KEY|printf.*TLS_KEY_PATH' "${ROOT}/templates/apply-panel-config.sh"

printf '\n\033[1;34m== 脚本自身可被 bash 解析 ==\033[0m\n'
for f in templates/apply-panel-config.sh templates/panel-validate.sh \
         templates/panel-nginx.sh templates/panel.conf.template \
         templates/apply-config.sh templates/apply-validate.sh; do
  if bash -n "${ROOT}/${f}" 2>/dev/null; then ok "语法 OK：${f}"; else bad "语法错误：${f}"; fi
done

printf '\n\033[1;34m==== 结果：通过 %d，失败 %d ====\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
