#!/usr/bin/env bash
#
# 部署后实机自检：逐条验证本次修复在**真实服务器**上生效。
#
# 只读：不修改任何配置、不重启服务、不吊销证书。
# 刻意不去触发登录失败，避免把管理员账号打进限速锁定。
#
# 用法：
#   sudo ./deploy/verify.sh
#   PANEL_PORT=8000 PANEL_HOME=/opt/ovpnmanager ./deploy/verify.sh
set -uo pipefail

PANEL_HOME="${PANEL_HOME:-/opt/ovpnmanager}"
PANEL_PORT="${PANEL_PORT:-8000}"
ENV_FILE="${ENV_FILE:-${PANEL_HOME}/backend/.env}"
OVPN_SERVICE="${OVPN_SERVICE:-openvpn@server}"
PANEL_SERVICE="${PANEL_SERVICE:-ovpnmanager}"
BASE="http://127.0.0.1:${PANEL_PORT}"
WEB="http://127.0.0.1"

pass=0; fail=0; skip=0
ok()   { printf '  \033[1;32m[PASS]\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[1;31m[FAIL]\033[0m %s\n' "$*"; fail=$((fail+1)); }
info() { printf '  \033[1;33m[SKIP]\033[0m %s\n' "$*"; skip=$((skip+1)); }
head_() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }

check() { # check <描述> <期望> <实际>
  if [ "$2" = "$3" ]; then ok "$1（=$3）"; else bad "$1：期望 $2，实际 $3"; fi
}

# 从 .env 读一个键
env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- ; }

DB_PATH="$(env_get SQLITE_PATH)"
[ -n "$DB_PATH" ] || DB_PATH="$(env_get OVPNM_DB_PATH)"
[ -n "$DB_PATH" ] || DB_PATH="${PANEL_HOME}/backend/data/app.db"

# --------------------------------------------------------------------------- #
head_ "1. 服务与就绪"
for svc in "$PANEL_SERVICE" "$OVPN_SERVICE" nginx; do
  state="$(systemctl is-active "$svc" 2>&1 || true)"
  if [ "$state" = "active" ]; then ok "$svc 运行中"; else bad "$svc 状态为 $state"; fi
done

ready="$(curl -fsS --max-time 10 "${BASE}/api/health/ready" 2>/dev/null || echo '{}')"
if printf '%s' "$ready" | grep -q '"status":"ok"'; then
  ok "面板就绪探针正常：$ready"
else
  bad "面板就绪探针异常：$ready"
fi

diag="$(curl -fsS --max-time 10 "${BASE}/api/health/diagnostics" 2>/dev/null || echo '{}')"

# --------------------------------------------------------------------------- #
head_ "2. 修复(5)：服务状态不再误报 unknown"
active_state="$(systemctl show -p ActiveState --value "$OVPN_SERVICE" 2>/dev/null || echo '')"
panel_state="$(printf '%s' "$diag" | sed -n 's/.*"openvpn_service":"\([a-z]*\)".*/\1/p')"
if [ "$active_state" = "active" ]; then
  check "服务在运行，面板应报 running" "running" "$panel_state"
elif [ "$active_state" = "inactive" ] || [ "$active_state" = "failed" ]; then
  # 原实现在这里返回 unknown（systemctl status 退出码 3 被当成错误）
  check "服务已停止，面板应报 stopped（旧实现是 unknown）" "stopped" "$panel_state"
else
  info "OpenVPN ActiveState=$active_state，无法判定面板映射（面板报 $panel_state）"
fi

# --------------------------------------------------------------------------- #
head_ "3. 修复(4)：审计日志索引 + SQLite WAL"
PY="$(command -v python3 || true)"
if [ -n "$PY" ] && [ -f "$DB_PATH" ]; then
  idx="$("$PY" - "$DB_PATH" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
print(",".join(sorted(n for n in names if "auditlog" in n)))
PYEOF
)"
  if printf '%s' "$idx" | grep -q "ix_auditlog_created_at"; then
    ok "auditlog.created_at 索引存在（$idx）"
  else
    bad "缺少 auditlog.created_at 索引（现有：${idx:-无}）"
  fi
  journal="$("$PY" - "$DB_PATH" <<'PYEOF'
import sqlite3, sys
print(sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True).execute("PRAGMA journal_mode").fetchone()[0])
PYEOF
)"
  check "SQLite 处于 WAL 模式（读写不再互相阻塞）" "wal" "$(printf '%s' "$journal" | tr 'A-Z' 'a-z')"
else
  info "跳过：需要 python3 且数据库存在（DB_PATH=$DB_PATH）"
fi

# --------------------------------------------------------------------------- #
head_ "4. 修复(1)：307 重定向与 CORS 预检"
# 尾斜杠路由：不带斜杠应被重定向，带斜杠应落到鉴权层（401）而不是 307
code_noslash="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/api/servers" || echo 000)"
code_slash="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/api/servers/" || echo 000)"
if [ "$code_noslash" = "307" ] && [ "$code_slash" != "307" ]; then
  ok "路由行为符合预期（/api/servers=$code_noslash，/api/servers/=$code_slash）"
  printf '       注意：前端已改为请求带斜杠的地址，因此访问日志里不应再出现 307\n'
else
  bad "路由行为异常（/api/servers=$code_noslash，/api/servers/=$code_slash）"
fi

# 前端构建产物里不应该还有裸的 "/servers" 或 "/clients"
asset="$(ls -1 "${WEB}/ovpnmanager/assets"/index-*.js 2>/dev/null | head -1 || true)"
if [ -n "$asset" ]; then
  if grep -q '"/servers/"' "$asset" && grep -q '"/clients/"' "$asset"; then
    ok "前端产物使用带斜杠的列表地址（消除 307）"
  else
    bad "前端产物里找不到带斜杠的列表地址：$asset"
  fi
else
  info "未找到前端产物（跳过产物级检查）"
fi

# 同源反代：经 nginx 访问 API 不应需要预检
if curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${WEB}/api/health/live" | grep -q 200; then
  ok "nginx 同源反代 /api 可用（浏览器不再发 CORS 预检）"
else
  bad "nginx /api 反代不可用"
fi

# nginx 是否开启 gzip
enc="$(curl -sI --max-time 10 -H 'Accept-Encoding: gzip' "${WEB}/ovpnmanager/" | tr -d '\r' | awk 'tolower($1)=="content-encoding:"{print $2}' | head -1)"
if [ "$enc" = "gzip" ]; then ok "nginx 已对前端开启 gzip"; else info "首页未返回 gzip（可能太小未达阈值），请手动确认 gzip 配置：$enc"; fi

# --------------------------------------------------------------------------- #
head_ "5. 修复(11)：前端产物体积"
if [ -d "${WEB}/ovpnmanager/assets" ]; then
  total="$(du -sb "${WEB}/ovpnmanager" | cut -f1)"
  printf '       产物总大小：%s 字节（修复前约 1,439,385）\n' "$total"
  if [ "$total" -lt 1200000 ]; then ok "产物已瘦身"; else bad "产物仍偏大：$total 字节"; fi
else
  info "未找到 ${WEB}/ovpnmanager/assets"
fi

# --------------------------------------------------------------------------- #
head_ "6. 修复(2)：management 可达性与熔断"
if printf '%s' "$diag" | grep -q '"reachable":true'; then
  ok "management 接口可达"
else
  bad "management 接口不可达：$(printf '%s' "$diag" | head -c 300)"
  printf '       请确认 server.conf 里有 management 127.0.0.1 7505\n'
fi
if printf '%s' "$diag" | grep -q '"reachable":false.*熔断\|熔断中'; then
  info "当前处于熔断期（属正常降级行为）"
fi

# 访问日志里最近是否还有 307 / OPTIONS
if command -v journalctl >/dev/null 2>&1; then
  recent="$(journalctl -u "$PANEL_SERVICE" --since '15 min ago' --no-pager 2>/dev/null || true)"
  n307="$(printf '%s' "$recent" | grep -c ' 307 ' || true)"
  nopt="$(printf '%s' "$recent" | grep -c 'OPTIONS ' || true)"
  if [ "${n307:-0}" -eq 0 ]; then ok "最近 15 分钟访问日志无 307 重定向"; else bad "最近 15 分钟仍有 $n307 条 307"; fi
  if [ "${nopt:-0}" -eq 0 ]; then ok "最近 15 分钟访问日志无 OPTIONS 预检"; else bad "最近 15 分钟仍有 $nopt 条 OPTIONS"; fi
else
  info "无 journalctl，跳过访问日志检查"
fi

# --------------------------------------------------------------------------- #
head_ "7. 修复(6)：吊销不再重启整个服务"
if grep -q 'REVOKE_RESTARTS_SERVICE=true' "$ENV_FILE" 2>/dev/null; then
  bad "REVOKE_RESTARTS_SERVICE=true：吊销仍会断开所有在线用户"
else
  ok "REVOKE_RESTARTS_SERVICE 未开启（吊销只重读 CRL 并单独踢该客户端）"
fi
if grep -q 'crl-verify' /etc/openvpn/server.conf 2>/dev/null; then
  ok "server.conf 配置了 crl-verify"
else
  info "未在 /etc/openvpn/server.conf 找到 crl-verify"
fi

# --------------------------------------------------------------------------- #
head_ "8. 修复(9)：安全"
sec="$(printf '%s' "$diag" | sed -n 's/.*"security_warnings":\[\(.*\)\].*/\1/p')"
if [ -z "$sec" ]; then
  ok "无安全自检告警（SECRET_KEY 与口令均非公开默认值）"
else
  bad "存在安全告警：$sec"
fi

secret="$(env_get SECRET_KEY)"
case "$secret" in
  qa111-wsx-111-edc-rfv-111|change-me-in-prod|"")
    bad "SECRET_KEY 仍是公开默认值，请执行 deploy/rotate-secret.sh 轮换" ;;
  *) ok "SECRET_KEY 不是公开默认值" ;;
esac

run_user="$(systemctl show -p User --value "$PANEL_SERVICE" 2>/dev/null || echo '')"
if [ "$run_user" = "root" ] || [ -z "$run_user" ]; then
  bad "面板以 root 运行（建议使用 install.sh 的默认降权配置）"
else
  ok "面板以非 root 用户运行（$run_user）"
fi

if [ -x /usr/local/lib/ovpnmanager/systemctl-wrapper ]; then
  ok "systemctl 包装脚本已安装"
else
  info "未安装 systemctl 包装脚本（可能是 RUN_AS_ROOT=1 部署）"
fi

env_perm="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
if [ "$env_perm" = "600" ]; then ok ".env 权限 0600"; else info ".env 权限为 $env_perm（建议 600）"; fi

# 前端只对 VPN 网段开放
nginx_conf="/etc/nginx/conf.d/ovpnmanager.conf"
[ -f "$nginx_conf" ] || nginx_conf="/etc/nginx/sites-available/ovpnmanager.conf"
if [ -f "$nginx_conf" ]; then
  if grep -qE '^\s*deny all;' "$nginx_conf"; then
    ok "nginx 限制面板访问来源（$(grep -E '^\s*allow ' "$nginx_conf" | head -2 | tr -d ' ' | tr '\n' ' '))"
  else
    bad "nginx 未限制面板访问来源"
  fi
fi

# --------------------------------------------------------------------------- #
head_ "9. 修复(10)：隧道配置"
conf=/etc/openvpn/server.conf
if [ -f "$conf" ]; then
  proto="$(grep -E '^proto ' "$conf" | awk '{print $2}' | head -1)"
  if [ "$proto" = "udp" ]; then
    ok "proto=udp（避免 TCP over TCP 的吞吐塌陷）"
  else
    info "proto=$proto：若链路有丢包，TCP 会明显更慢；仅在防火墙只放行 TCP 时保留"
  fi
  grep -qE '^tls-crypt' "$conf" && ok "使用 tls-crypt" || info "未使用 tls-crypt（tls-auth 也可用）"
  grep -qE '^sndbuf' "$conf" && ok "已配置 sndbuf/rcvbuf 缓冲调优" || info "未配置 sndbuf/rcvbuf"
  grep -qE '^data-ciphers' "$conf" && ok "已配置 data-ciphers 协商列表" || info "未配置 data-ciphers"
else
  info "未找到 $conf"
fi

# --------------------------------------------------------------------------- #
printf '\n\033[1;34m==== 结果：通过 %d，失败 %d，跳过 %d ====\033[0m\n' "$pass" "$fail" "$skip"
if [ "$fail" -gt 0 ]; then
  printf '\033[1;31m有 %d 项未通过，请按上面的提示处理。\033[0m\n' "$fail"
  exit 1
fi
printf '\033[1;32m全部检查通过。\033[0m\n'
