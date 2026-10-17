#!/bin/bash
# 容器入口：没有 systemd，所以这里就是"服务管理器"。
# 职责：首次启动初始化 PKI 与 server.conf -> 拉起 openvpn / nginx / 面板 -> 收信号退出。
set -uo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }

OPENVPN_DIR=/etc/openvpn
SERVER_CONF="$OPENVPN_DIR/server.conf"
EASYRSA_DIR="$OPENVPN_DIR/easy-rsa"
PANEL_DIR=/opt/ovpnmanager
WEB_PORT="${PANEL_WEB_PORT:-5698}"
VPN_NETWORK="${VPN_NETWORK:-10.8.44.0}"
VPN_NETMASK="${VPN_NETMASK:-255.255.255.0}"
VPN_PORT="${VPN_PORT:-1194}"

# ---------------------------------------------------------------- 首次初始化
init_pki() {
  if [ -f "$EASYRSA_DIR/pki/ca.crt" ]; then return 0; fi
  log "初始化 PKI（首次启动，可能需要一两分钟）"
  mkdir -p "$EASYRSA_DIR"
  cp -r /usr/share/easy-rsa/* "$EASYRSA_DIR"/ 2>/dev/null || true
  cd "$EASYRSA_DIR" || exit 1
  ./easyrsa --batch --req-cn=ovpn-oneclick-ca init-pki >/dev/null 2>&1
  ./easyrsa --batch --req-cn=ovpn-oneclick-ca build-ca nopass >/dev/null 2>&1 || { log "build-ca 失败"; exit 1; }
  ./easyrsa --batch gen-dh >/dev/null 2>&1
  ./easyrsa --batch build-server-full server nopass >/dev/null 2>&1 || { log "签发服务端证书失败"; exit 1; }
  ./easyrsa --batch gen-crl >/dev/null 2>&1
  openvpn --genkey secret "$OPENVPN_DIR/ta.key"
  mkdir -p "$OPENVPN_DIR/server"
  cp pki/ca.crt pki/issued/server.crt pki/private/server.key pki/dh.pem "$OPENVPN_DIR/server/"
  cp pki/crl.pem "$OPENVPN_DIR/server/crl.pem"
  cp "$OPENVPN_DIR/ta.key" "$OPENVPN_DIR/server/ta.key"
  log "PKI 就绪"
}

init_conf() {
  if [ -f "$SERVER_CONF" ]; then return 0; fi
  log "生成 server.conf（$VPN_NETWORK/$VPN_NETMASK，$VPN_PORT/udp）"
  cat > "$SERVER_CONF" <<EOF
port $VPN_PORT
proto udp
dev tun
topology subnet
server $VPN_NETWORK $VPN_NETMASK
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
ca $OPENVPN_DIR/server/ca.crt
cert $OPENVPN_DIR/server/server.crt
key $OPENVPN_DIR/server/server.key
dh $OPENVPN_DIR/server/dh.pem
tls-crypt $OPENVPN_DIR/server/ta.key
crl-verify $OPENVPN_DIR/server/crl.pem
user nobody
group nogroup
persist-key
persist-tun
keepalive 10 120
max-clients 64
client-to-client
explicit-exit-notify 1
status /var/log/openvpn/openvpn-status.log
log-append /var/log/openvpn/openvpn.log
management 127.0.0.1 7505
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 8.8.8.8"
push "dhcp-option DNS 8.8.4.4"

# >>> ovpn-oneclick managed >>>
# 以下指令由面板维护；块外内容原样保留（与宿主机安装同一套约定）
# <<< ovpn-oneclick managed <<<
EOF
}

init_forwarding() {
  # 容器里 ip_forward 默认跟随宿主/命名空间，显式打开；NAT 依赖宿主
  sysctl -qw net.ipv4.ip_forward=1 2>/dev/null || echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
  local egress
  egress="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
  [ -n "$egress" ] || { log "找不到默认出口网卡，跳过 NAT（split tunnel 仍可用）"; return 0; }
  iptables -t nat -C POSTROUTING -s "$VPN_NETWORK/24" -o "$egress" -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -s "$VPN_NETWORK/24" -o "$egress" -j MASQUERADE 2>/dev/null \
    || log "写 NAT 规则失败（容器可能缺少 NET_ADMIN）"
}

init_panel() {
  if [ -x "$PANEL_DIR/backend/.venv/bin/uvicorn" ]; then return 0; fi
  log "安装面板程序到 $PANEL_DIR"
  mkdir -p "$PANEL_DIR"
  tar -xzf /tmp/panel.tar.gz -C /tmp
  cp -a /tmp/ovpnmanager-panel/. "$PANEL_DIR/"
  python3 -m venv "$PANEL_DIR/backend/.venv"
  "$PANEL_DIR/backend/.venv/bin/pip" install -q --upgrade pip
  "$PANEL_DIR/backend/.venv/bin/pip" install -q -r "$PANEL_DIR/backend/requirements.txt"
  mkdir -p "$PANEL_DIR/backend/data"
  chown -R ovpnpanel:ovpnpanel "$PANEL_DIR"
}

# ---------------------------------------------------------------- 启动
mkdir -p /var/log/openvpn
init_pki
init_conf
init_forwarding
init_panel

log "启动 OpenVPN"
openvpn --config "$SERVER_CONF" --daemon --writepid /run/openvpn.pid
sleep 2
if ! pgrep -x openvpn >/dev/null; then
  log "OpenVPN 未能启动，打印日志后退出"
  tail -30 /var/log/openvpn/openvpn.log 2>/dev/null
  exit 1
fi

log "启动面板（127.0.0.1:8000，以 ovpnpanel 运行）"
cd "$PANEL_DIR/backend" || exit 1
setpriv --reuid=ovpnpanel --regid=ovpnpanel --init-groups \
  env HOME=/home/ovpnpanel PATH="$PANEL_DIR/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin" \
  "$PANEL_DIR/backend/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 \
  >>/var/log/panel.log 2>&1 &
PANEL_PID=$!

log "启动 nginx（对外 $WEB_PORT）"
nginx -g 'daemon off;' >>/var/log/nginx.log 2>&1 &
NGINX_PID=$!

log "就绪：面板 http://<host>:$WEB_PORT/ovpnmanager/   OpenVPN udp/$VPN_PORT"

term() {
  log "收到退出信号，停止服务"
  kill "$PANEL_PID" "$NGINX_PID" 2>/dev/null
  openvpn_pid="$(cat /run/openvpn.pid 2>/dev/null || true)"
  [ -n "$openvpn_pid" ] && kill "$openvpn_pid" 2>/dev/null
  wait 2>/dev/null
  exit 0
}
trap term TERM INT

# 任一关键进程退出就整体退出（容器应该被重启，而不是半死不活）
while true; do
  sleep 5
  kill -0 "$PANEL_PID" 2>/dev/null || { log "面板进程退出"; term; }
  kill -0 "$NGINX_PID" 2>/dev/null || { log "nginx 退出"; term; }
  pgrep -x openvpn >/dev/null || { log "OpenVPN 退出"; term; }
done
