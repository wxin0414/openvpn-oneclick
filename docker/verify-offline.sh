#!/bin/bash
# 离线验证 Docker 部署的三个"可离线检查"部分——不需要装 Docker、不需要联网。
#
# 检查什么：
#   1. 从 docker/entrypoint.sh 抽出真实的 init_conf，生成一份 server.conf
#   2. 用 openvpn 自己的选项表逐条核对生成的指令（含 push 载荷）
#   3. 用面板自己的解析器读这份配置，确认托管块与 29 个可编辑项都对
#   4. 容器 nginx 配置跑 nginx -t
#   5. docker-compose.yml 能否解析、关键字段是否齐全
#
# 不检查什么（需要 Docker 守护进程）：镜像构建、容器启动、运行期行为。
# 用法：sudo bash docker/verify-offline.sh   （需要已安装 openvpn / nginx / 面板）
set -o pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
pass=0; fail=0
ok()  { printf '  [PASS] %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }

echo "=== 1) 生成 server.conf（复用 entrypoint 里真实的 init_conf） ==="
OPENVPN_DIR="$W/etc/openvpn"; SERVER_CONF="$OPENVPN_DIR/server.conf"
VPN_NETWORK="10.8.44.0"; VPN_NETMASK="255.255.255.0"; VPN_PORT="1194"
export OPENVPN_DIR SERVER_CONF VPN_NETWORK VPN_NETMASK VPN_PORT
log() { :; }   # entrypoint 的 log 函数，抽出来单跑时用空实现
eval "$(sed -n '/^log() {/,/^}$/p' "$REPO_DIR/docker/entrypoint.sh")"
eval "$(sed -n '/^init_conf() {/,/^}$/p' "$REPO_DIR/docker/entrypoint.sh")"
mkdir -p "$OPENVPN_DIR"
# 安全闸门：必须在**写入之前**确认目标落在临时目录里。
# 教训：早先版本先 rm -f 再生成、事后才断言，结果一次跑偏直接把真机
# /etc/openvpn/server.conf 覆盖成了模板（已从 .bak 恢复）。任何"会写文件"的
# 自检脚本都必须先拒绝、再执行。
case "$SERVER_CONF" in
  "$W"/*) : ;;
  *) echo "  [ABORT] SERVER_CONF=$SERVER_CONF 不在临时目录 $W 下，拒绝继续"; exit 2 ;;
esac
rm -f "$SERVER_CONF"
init_conf && ok "生成 $SERVER_CONF（$(wc -l < "$SERVER_CONF") 行）" || bad "生成失败"

echo "=== 2) 用 openvpn 的选项表核对（含 push 载荷） ==="
openvpn --help 2>/dev/null \
  | awk '/^--[a-z]/{gsub(/^--/,"",$0); split($0,a,/[ =[]/); print a[1]}' | sort -u > "$W/known.txt"
echo "  openvpn 认识 $(wc -l < "$W/known.txt") 个选项"
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  first="${line%% *}"
  if [ "$first" = "push" ]; then
    # push "xxx yyy"：去掉首尾引号后取载荷首词
    payload="${line#push }"
    payload="${payload#\"}"; payload="${payload%\"}"
    first="${payload%% *}"
  fi
  grep -qx "$first" "$W/known.txt" || bad "未知指令: $line"
done < "$SERVER_CONF"
ok "全部指令都在 openvpn 选项表内"

echo "=== 3) 托管块与面板解析 ==="
grep -q '>>> ovpn-oneclick managed >>>' "$SERVER_CONF" && ok "起始标记" || bad "缺起始标记"
grep -q '<<< ovpn-oneclick managed <<<' "$SERVER_CONF" && ok "结束标记" || bad "缺结束标记"
PANEL_BACKEND="${PANEL_BACKEND:-/opt/ovpnmanager/backend}"
if [ -x "$PANEL_BACKEND/.venv/bin/python" ]; then
  TMPCONF="$(dirname "$SERVER_CONF")/panel-parse.conf"
  cp "$SERVER_CONF" "$TMPCONF"
  ( cd "$PANEL_BACKEND" && SERVER_CONF_PATH="$TMPCONF" ./.venv/bin/python - <<'PY'
import os, sys
sys.path.insert(0, '.')
from app.services import server_conf
c = server_conf.load()
s = server_conf.editable_settings(c, server_conf.parse_push_directives(c.push_directives))
print(f"  解析: port={c.port} proto={c.proto} net={c.network} topology={c.topology} 可编辑 {len(s)} 项 dns={s.get('dns_servers')}")
assert c.port == 1194 and c.proto == "udp" and len(s) >= 29, "解析结果不符合预期"
print("  [PASS] 面板能正确解析容器生成的配置")
PY
  ) || bad "面板解析失败"
else
  echo "  [SKIP] 找不到面板后端（$PANEL_BACKEND）"
fi

echo "=== 4) 容器 nginx 配置 ==="
if command -v nginx >/dev/null; then
  sed -e 's#^pid /run/nginx.pid;#pid /tmp/ngx-offline.pid;#' \
      -e 's#listen 5698;#listen 15699;#' \
      -e 's#^error_log .*#error_log /tmp/ngx-offline-error.log;#' \
      "$REPO_DIR/docker/nginx.conf" > "$W/nginx.conf"
  if nginx -t -c "$W/nginx.conf" >/dev/null 2>&1; then ok "nginx -t 通过"; else bad "nginx -t 失败"; nginx -t -c "$W/nginx.conf" 2>&1 | sed 's/^/     /'; fi
else
  echo "  [SKIP] 没有 nginx"
fi

echo "=== 5) docker-compose.yml ==="
if python3 -c 'import yaml' 2>/dev/null; then
  python3 - "$REPO_DIR/docker-compose.yml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
svc = (d.get("services") or {}).get("ovpn") or {}
assert svc, "缺少 services.ovpn"
assert any("1194" in str(p) for p in svc.get("ports") or []), "缺少 1194 端口映射"
assert "NET_ADMIN" in (svc.get("cap_add") or []), "缺少 NET_ADMIN"
assert len(svc.get("volumes") or []) >= 3, "卷不完整"
print(f"  services={list(d['services'])} ports={svc['ports']} volumes={len(svc['volumes'])} cap_add={svc['cap_add']}")
print("  [PASS] compose 关键字段齐全")
PY
else
  echo "  [SKIP] 没有 PyYAML"
fi

echo "=== 6) Dockerfile 静态审查（构建前就能发现的坑） ==="
DF="$REPO_DIR/docker/Dockerfile"
# 6a) 安装循环里的每个助手都必须真实存在于 templates/
missing=0
for name in $(sed -n '/for name in/,/; do/p' "$DF" | grep -oE '[a-z][a-z0-9-]+' | grep -vE '^(for|name|in|do)$'); do
  [ -f "$REPO_DIR/templates/$name.sh" ] || { bad "Dockerfile 要装 $name.sh，但 templates 里没有"; missing=$((missing+1)); }
done
[ "$missing" -eq 0 ] && ok "安装循环引用的助手都在 templates/ 里"

# 6b) sudoers 里放行的每个路径，必须由安装循环产出（名字不带 .sh）
sg=0
for path in $(grep -oE '/usr/local/lib/ovpnmanager/[a-z-]+' "$DF" | sort -u); do
  base="${path##*/}"
  grep -qE "(^|[[:space:]])$base([[:space:]]|\\\\)" "$DF" || { bad "sudoers 放行 $path，但安装循环没有 $base"; sg=$((sg+1)); }
done
[ "$sg" -eq 0 ] && ok "sudoers 放行的脚本都按无后缀名安装"

# 6c) 面板期望的助手路径必须与镜像一致
EXPECTED="$(grep -rhoE '/usr/local/lib/ovpnmanager/[a-z-]+' "$PANEL_BACKEND/app/services/server_conf_writer.py" 2>/dev/null | head -1)"
if [ -n "$EXPECTED" ]; then
  base="${EXPECTED##*/}"
  grep -qE "(^|[[:space:]])$base([[:space:]]|\\\\)" "$DF" && ok "面板期望的 $EXPECTED 与镜像安装名一致" || bad "面板期望 $EXPECTED，但镜像安装名不一致"
fi

# 6d) compose 端口必须与 EXPOSE 对齐
for p in $(grep -oE '^EXPOSE [0-9]+/[a-z]+' "$DF" | awk '{print $2}'); do
  num="${p%%/*}"
  grep -q "\"$num:" "$REPO_DIR/docker-compose.yml" && ok "EXPOSE $p 在 compose 里有映射" || bad "EXPOSE $p 未在 compose 里映射"
done

printf '\n==== 离线验证：通过 %d，失败 %d ====\n' "$pass" "$fail"
echo "注意：镜像构建与容器运行期行为需要 Docker 守护进程，本脚本不覆盖。"
[ "$fail" -eq 0 ]
