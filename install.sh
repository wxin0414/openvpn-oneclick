#!/usr/bin/env bash
#
# =============================================================================
#  ovpn-oneclick —— OpenVPN 服务端 + 管理面板 一键安装脚本
# =============================================================================
#
#  一条命令装好：OpenVPN 服务端（含证书体系、NAT 转发、客户端管理）
#                + ovpnmanager 可视化管理面板（Web 界面管理客户端/证书/服务）
#
#  本脚本是自包含的：面板代码与前端构建产物随本脚本一起发布在同一个仓库里，
#  安装时只从本仓库下载，**不依赖任何第三方 GitHub 项目**。
#
#  快速开始：
#      wget -O install.sh https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh
#      sudo bash install.sh --auto
#
#  完整用法：sudo bash install.sh --help
#
#  支持：Debian/Ubuntu、RHEL/CentOS/Rocky/AlmaLinux/Fedora
#
#  许可证：MIT（见 LICENSE）。OpenVPN 服务端安装部分为独立实现，
#          交互与命令行设计参考了 hwdsl2/openvpn-install（MIT）与
#          Nyr/openvpn-install（MIT），在此致谢。
# =============================================================================

set -Eeuo pipefail

SCRIPT_VERSION="1.2.4"

# ---------------------------------------------------------------------------
# 下载源（可用环境变量覆盖）
#
# 版本与仓库解耦：本脚本自身带 SCRIPT_VERSION，远端版本号来自仓库里的 VERSION 文件。
# 升级时先取 VERSION 比较，再决定是否下载载荷，因此**推送新版本不会让老装机无法升级**
# ——老脚本永远能把新版本拉下来（见 do_update / self_update）。
# ---------------------------------------------------------------------------
REPO_HOST="${REPO_HOST:-github}"
REPO_OWNER="${REPO_OWNER:-wxin0414}"
REPO_NAME="${REPO_NAME:-openvpn-oneclick}"
REPO_BRANCH="${REPO_BRANCH:-main}"
ASSET_PATH="${ASSET_PATH:-panel/ovpnmanager-panel.tar.gz}"
# 版本文件名必须带后缀：某些平台的 raw 路径对**无扩展名**的文件会返回 404
#（文件确实在仓库里，API 能看到），Gitee 上实测如此。
VERSION_PATH="${VERSION_PATH:-VERSION.txt}"

# 下载源按托管平台推导，两个平台都支持，任选其一即可：
#   github -> https://raw.githubusercontent.com/<owner>/<repo>/<branch>
#   gitee  -> https://gitee.com/<owner>/<repo>/raw/<branch>
# 想换成自建/对象存储时，直接覆盖 ASSET_BASE（或分别覆盖下面三个 URL）即可。
ASSET_BASE="${ASSET_BASE:-${GITEE_BASE:-}}"
if [ -z "$ASSET_BASE" ]; then
  case "$REPO_HOST" in
    github) ASSET_BASE="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}" ;;
    gitee)  ASSET_BASE="https://gitee.com/${REPO_OWNER}/${REPO_NAME}/raw/${REPO_BRANCH}" ;;
    *)
      printf 'REPO_HOST=%s 不支持（只支持 github 或 gitee）。\n也可以直接指定下载源：\n  ASSET_BASE=https://你的域名/ovpn bash install.sh --auto\n或分别指定 PANEL_TARBALL_URL / REMOTE_VERSION_URL / SELF_URL。\n' "$REPO_HOST" >&2
      exit 2
      ;;
  esac
fi
PANEL_TARBALL_URL="${PANEL_TARBALL_URL:-${ASSET_BASE}/${ASSET_PATH}}"
REMOTE_VERSION_URL="${REMOTE_VERSION_URL:-${ASSET_BASE}/${VERSION_PATH}}"
SELF_URL="${SELF_URL:-${ASSET_BASE}/install.sh}"

# 安装状态（记录已安装版本与关键路径，供 --update 使用）
STATE_DIR="${STATE_DIR:-/etc/ovpn-oneclick}"
STATE_FILE="${STATE_DIR}/state.conf"
PANEL_BACKUP_DIR="${PANEL_BACKUP_DIR:-/var/backups/ovpn-oneclick}"

# ---------------------------------------------------------------------------
# 默认配置（都可用命令行参数覆盖）
# ---------------------------------------------------------------------------
VPN_PROTO="${VPN_PROTO:-udp}"
VPN_PORT="${VPN_PORT:-1194}"
VPN_NETWORK="${VPN_NETWORK:-10.8.44.0}"
VPN_NETMASK="${VPN_NETMASK:-255.255.255.0}"
VPN_CIDR="${VPN_CIDR:-24}"
CLIENT_NAME="${CLIENT_NAME:-client}"
DNS1="${DNS1:-8.8.8.8}"
DNS2="${DNS2:-8.8.4.4}"
SERVER_ADDR="${SERVER_ADDR:-}"
SERVER_CN="${SERVER_CN:-server}"
MGMT_PORT="${MGMT_PORT:-7505}"

PANEL_ENABLE=1
PANEL_PORT="${PANEL_PORT:-8000}"
# 对外访问端口（nginx 监听）。默认 5698，**不再是 80**：
#   - 80 常被同机其它站点占用，且许多云厂商对 80/443 有备案或拦截要求；
#   - 面板是管理入口，单开一个端口更便于安全组只放行这一条。
# 内部 uvicorn 仍然只监听 127.0.0.1:${PANEL_PORT}，不对外暴露。
# 需要改端口时用环境变量覆盖：PANEL_WEB_PORT=8080 bash install.sh --auto
PANEL_WEB_PORT="${PANEL_WEB_PORT:-5698}"
# 额外允许访问面板的来源，逗号分隔，例如：
#   PANEL_ALLOW="203.0.113.7,198.51.100.0/24" bash install.sh --auto
# 面板默认只允许 VPN 隧道网段与本机（见 nginx 模板）；要从公网直连就得把来源
# 加到这里。注意：allow/deny 是"首次匹配生效"，所以这些行会插在 deny all 之前。
PANEL_ALLOW="${PANEL_ALLOW:-}"
# ---- 面板自身的网络与安全设置（「面板设置」页的初始值）----
#
# 为什么需要"持久化"这一层：这些值都能在面板里改（见 templates/apply-panel-config.sh），
# 而重装 / --update 会重新渲染 nginx。如果渲染时还用 build 默认值，用户改过的端口
# 就会在一次升级后**静默退回默认**，面板可能再也连不上（端口变了、安全组没开）。
# 因此落地结果被写进 ${STATE_DIR}/panel.state，安装脚本渲染时**优先读它**。
#
# 取值优先级：panel.state > 下面的环境变量 / 默认值。
PANEL_STATE_FILE="${PANEL_STATE_FILE:-${STATE_DIR}/panel.state}"
# 绑定域名（nginx server_name，留空= _）
PANEL_SERVER_NAME="${PANEL_SERVER_NAME:-_}"
# 放行来源模式：vpn / public / custom（留空=由 PANEL_ALLOW 推导，见 derive_panel_allow）
PANEL_ALLOW_MODE="${PANEL_ALLOW_MODE:-}"
PANEL_ALLOW_CIDRS="${PANEL_ALLOW_CIDRS:-}"
# TLS：off / selfsigned / existing（existing 时需同时给 cert 与 key）
PANEL_TLS_MODE="${PANEL_TLS_MODE:-off}"
PANEL_TLS_CERT="${PANEL_TLS_CERT:-}"
PANEL_TLS_KEY="${PANEL_TLS_KEY:-}"
# 面板 TLS 相关目录与 nginx 配置备份目录（root 所有）
PANEL_TLS_DIR="${PANEL_TLS_DIR:-${STATE_DIR}/tls}"
PANEL_BACKUP_DIR="${PANEL_BACKUP_DIR:-/var/backups/ovpn-oneclick/panel}"
# 面板访问协议（由 TLS 模式推导，用于打印地址与自检）
PANEL_SCHEME="${PANEL_SCHEME:-http}"
PANEL_USER="${PANEL_USER:-ovpnpanel}"
PANEL_HOME="${PANEL_HOME:-/opt/ovpnmanager}"
WEB_ROOT="${WEB_ROOT:-/var/www}"
WEB_NAME="ovpnmanager"

FULL_TUNNEL=1
EXTRA_ROUTES=()

AUTO_INSTALL=0
ASSUME_YES=0
ACTION="install"
# 机器上已有 OpenVPN 时的策略：默认拒绝改动（检测见 detect_existing_install）
ADOPT_EXISTING=0
FORCE_REWRITE=0
# IPv6 泄漏防护（客户端双栈时绕开 IPv4-only VPN）
BLOCK_IPV6=0
# 用户是否显式指定了 DNS（分流模式下不该默认推公共 DNS）
DNS_EXPLICIT=0
# 已存在的 tls-crypt / tls-auth 密钥（采纳既有安装时必须复用，否则所有客户端配置失效）
EXISTING_TLS_KEY=""
EXISTING_TLS_MODE=""
EXISTING_SERVER_CONF=""
# 自更新成功后记录新脚本版本：否则 save_state 会把**旧**版本写进状态文件，
# 于是 --check-update 永远提示"有新版本"。见 self_update / save_state。
UPDATED_VERSION=""

OVPN_DIR="/etc/openvpn"
EASYRSA_DIR="/etc/openvpn/easy-rsa"
CCD_DIR="/etc/openvpn/ccd"
CLIENT_EXPORT_DIR="/etc/openvpn/client"
LOG_DIR="/var/log/openvpn"
SERVER_DIR="/etc/openvpn/server"

OVPN_SERVICE=""
SERVER_CONF=""
CRL_PATH=""
TA_KEY=""
# OpenVPN 降权后以该用户/组运行（detect_os 会按发行版细化）
OVPN_USER="nobody"
OVPN_GROUP="nobody"

# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  C_RESET='\033[0m'; C_INFO='\033[1;34m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'
else
  C_RESET=''; C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''
fi
# 注意：info/ok/step 一律输出到 **stderr**。
# 原因：脚本里有若干函数（fetch_panel、detect_public_ip、find_easyrsa 等）用 stdout
# 返回字符串，调用方写 `x="$(f)"`。如果这些函数里的日志打进 stdout，就会和返回值
# 混在一起被一起捕获——实测出现过 `tar -xzf "$asset"` 的"路径"里混进一整段日志、
# 报 `Cannot open: No such file or directory` 的故障。
# 把日志固定到 stderr 后，stdout 只承载"返回值"，这类问题从根上消失。
info()  { printf "${C_INFO}[*]${C_RESET} %s\n" "$*" >&2; }
ok()    { printf "${C_OK}[+]${C_RESET} %s\n" "$*" >&2; }
warn()  { printf "${C_WARN}[!]${C_RESET} %s\n" "$*" >&2; }
die()   { printf "${C_ERR}[x]${C_RESET} %s\n" "$*" >&2; exit 1; }
step()  { printf "\n${C_INFO}==== %s ====${C_RESET}\n" "$*" >&2; }

trap 'die "安装中断（第 ${LINENO} 行）"' ERR

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
usage() {
  cat <<'EOF'
ovpn-oneclick —— OpenVPN 服务端 + 管理面板 一键安装脚本

用法: sudo bash install.sh [选项]

安装（不带参数为交互式；--auto 为全自动）:
  --auto                     全自动安装，使用默认值或已指定的选项
  --proto <udp|tcp>          OpenVPN 协议（默认 udp）
  --port <1-65535>           OpenVPN 端口（默认 1194）
  --serveraddr <IP|域名>     客户端连接使用的地址（默认自动探测公网 IPv4）
  --clientname <名称>        首个客户端名称（默认 client）
  --dns1 <IP> --dns2 <IP>    推送给客户端的 DNS（默认 8.8.8.8 / 8.8.4.4）
  --network <网段>           VPN 客户端网段前三位（默认 10.8.44.0，掩码 /24）
  --full-tunnel              客户端全局代理（默认）
  --split-tunnel             分流：只推送 --route 指定的路由
  --route <CIDR>             分流模式下推送的路由，可重复使用
  --block-ipv6               阻止客户端 IPv6 泄漏（推送 IPv6 黑洞，见 README）
  --panel-port <端口>        面板监听端口（默认 8000）
  --no-panel                 只安装 OpenVPN 服务端，不安装面板

已有 OpenVPN 的机器（例如先用过 hwdsl2/openvpn-install 的脚本）:
  --adopt                    采纳既有安装：复用其 PKI 与 tls-crypt/tls-auth 密钥，
                             并在其 server.conf 里补上面板需要的指令，不替换配置
  --force                    明确允许备份后重写既有 server.conf（仍会复用 PKI 与 tls 密钥）

升级:
  --check-update             只检查是否有新版本，不修改任何东西
  --update                   升级面板与模板到仓库中的最新版本（不动 PKI、
                             server.conf、SECRET_KEY 与管理员口令）

客户端管理:
  --addclient <名称>         新增客户端并导出 .ovpn
  --revokeclient <名称>      吊销客户端证书并刷新 CRL
  --listclients              列出已有客户端
  --exportclient <名称>      重新导出已有客户端的 .ovpn

其它:
  --uninstall                卸载 OpenVPN 与面板（会二次确认）
  -y, --yes                  对确认类提示自动回答 yes
  -h, --help                 显示本帮助
  --version                  显示版本

示例:
  sudo bash install.sh --auto
  sudo bash install.sh --proto tcp --port 443 --clientname myphone
  sudo bash install.sh --addclient laptop
  sudo bash install.sh --auto --no-panel --split-tunnel --route 192.168.1.0/24

安装完成后可再次运行本脚本管理客户端，或直接使用 Web 面板。
EOF
}

require_value() { # require_value <选项名> <值>
  [ "$#" -ge 2 ] && [ -n "${2:-}" ] || die "选项 $1 需要一个值"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --auto)        AUTO_INSTALL=1 ;;
    --proto)       require_value "$1" "${2:-}"; VPN_PROTO="${2,,}"; shift ;;
    --port)        require_value "$1" "${2:-}"; VPN_PORT="$2"; shift ;;
    --serveraddr)  require_value "$1" "${2:-}"; SERVER_ADDR="$2"; shift ;;
    --clientname)  require_value "$1" "${2:-}"; CLIENT_NAME="$2"; shift ;;
    --dns1)        require_value "$1" "${2:-}"; DNS1="$2"; DNS_EXPLICIT=1; shift ;;
    --dns2)        require_value "$1" "${2:-}"; DNS2="$2"; DNS_EXPLICIT=1; shift ;;
    --network)     require_value "$1" "${2:-}"; VPN_NETWORK="$2"; shift ;;
    --route)       require_value "$1" "${2:-}"; EXTRA_ROUTES+=("$2"); shift ;;
    --full-tunnel) FULL_TUNNEL=1 ;;
    --split-tunnel) FULL_TUNNEL=0 ;;
    --block-ipv6)  BLOCK_IPV6=1 ;;
    --panel-port)  require_value "$1" "${2:-}"; PANEL_PORT="$2"; shift ;;
    --no-panel)    PANEL_ENABLE=0 ;;
    --adopt)       ADOPT_EXISTING=1 ;;
    --force)       FORCE_REWRITE=1 ;;
    --check-update) ACTION="check-update" ;;
    --update)      ACTION="update" ;;
    --addclient)   require_value "$1" "${2:-}"; ACTION="addclient"; CLIENT_NAME="$2"; shift ;;
    --revokeclient) require_value "$1" "${2:-}"; ACTION="revokeclient"; CLIENT_NAME="$2"; shift ;;
    --exportclient) require_value "$1" "${2:-}"; ACTION="exportclient"; CLIENT_NAME="$2"; shift ;;
    --listclients) ACTION="listclients" ;;
    --uninstall)   ACTION="uninstall" ;;
    -y|--yes)      ASSUME_YES=1 ;;
    -h|--help)     usage; exit 0 ;;
    --version)     echo "ovpn-oneclick ${SCRIPT_VERSION}"; exit 0 ;;
    *)             die "未知选项：$1（用 --help 查看用法）" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
require_root() {
  [ "$(id -u)" -eq 0 ] || die "请以 root 运行（sudo bash $0 ...）"
}

detect_os() {
  step "探测系统环境"
  [ -r /etc/os-release ] || die "无法读取 /etc/os-release，不支持的系统"
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_LIKE="${ID_LIKE:-}"
  OS_VER="${VERSION_ID:-}"

  if   command -v dnf     >/dev/null 2>&1; then PKG=dnf
  elif command -v yum     >/dev/null 2>&1; then PKG=yum
  elif command -v apt-get >/dev/null 2>&1; then PKG=apt
  else die "只支持 dnf / yum / apt 的发行版（当前：${OS_ID}）"
  fi

  case "${OS_ID}${OS_LIKE}" in
    *debian*|*ubuntu*) OVPN_GROUP="nogroup" ;;
    *)                 OVPN_GROUP="nobody" ;;
  esac
  getent group "$OVPN_GROUP" >/dev/null 2>&1 || OVPN_GROUP="nobody"
  id -u "$OVPN_USER" >/dev/null 2>&1 || OVPN_USER="root"

  command -v systemctl >/dev/null 2>&1 || die "需要 systemd"
  info "发行版：${OS_ID} ${OS_VER}（包管理器：${PKG}，OpenVPN 运行组：${OVPN_GROUP}）"
}

pkg_install() {
  case "$PKG" in
    dnf) dnf install -y "$@" ;;
    yum) yum install -y "$@" ;;
    apt) DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
  esac
}

install_packages() {
  step "安装依赖包"
  case "$PKG" in
    dnf|yum)
      pkg_install epel-release >/dev/null 2>&1 || warn "epel-release 不可用（可能已启用）"
      pkg_install openvpn easy-rsa openssl nginx python3 python3-pip curl ca-certificates logrotate iptables rsync || die "依赖安装失败"
      pkg_install python3-virtualenv >/dev/null 2>&1 || true
      ;;
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq || warn "apt-get update 失败，继续尝试"
      pkg_install openvpn easy-rsa openssl nginx python3 python3-venv python3-pip curl ca-certificates logrotate iptables rsync || die "依赖安装失败"
      ;;
  esac
  command -v openvpn >/dev/null || die "openvpn 安装失败"
  command -v nginx   >/dev/null || die "nginx 安装失败"
  [ "$PANEL_ENABLE" = "1" ] && { python3 -c 'import venv' 2>/dev/null || die "python3 venv 不可用（请安装 python3-venv）"; }
  ok "依赖包就绪：$(openvpn --version 2>/dev/null | head -1)"
}

detect_public_ip() {
  local ip
  for url in https://api.ipify.org https://ifconfig.me/ip https://ipv4.icanhazip.com; do
    ip="$(curl -fsS --max-time 8 "$url" 2>/dev/null | tr -d '[:space:]')" || true
    if printf '%s' "$ip" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
      printf '%s' "$ip"; return 0
    fi
  done
  return 1
}

# 追加/导出客户端时（不是首次安装），SERVER_ADDR 是空的，必须补出来，
# 否则生成的 .ovpn 会写成 "remote  1194"（地址缺失）而无法连接。
resolve_server_addr() {
  [ -n "$SERVER_ADDR" ] && return 0
  local existing=""
  existing="$(find "$CLIENT_EXPORT_DIR" -maxdepth 1 -name '*.ovpn' -print -quit 2>/dev/null || true)"
  if [ -n "$existing" ] && [ -f "$existing" ]; then
    SERVER_ADDR="$(grep -oP '^\s*remote\s+\K\S+' "$existing" 2>/dev/null | head -1 || true)"
    if [ -n "$SERVER_ADDR" ]; then
      info "沿用已有客户端配置中的服务器地址：${SERVER_ADDR}"
      return 0
    fi
  fi
  SERVER_ADDR="$(detect_public_ip || true)"
  [ -n "$SERVER_ADDR" ] || die "无法确定服务器地址，请用 --serveraddr <IP或域名> 指定"
  info "自动探测到公网地址：${SERVER_ADDR}"
}

# 服务名与配置路径：Debian 系新包提供 openvpn-server@，RHEL 系用 openvpn@
resolve_openvpn_layout() {
  # 布局以**已有配置文件在哪**为准，而不是"哪个 unit 存在"。
  # Debian 12 的 openvpn 包同时提供 openvpn@.service 与 openvpn-server@.service；
  # 若只看 unit 是否存在，重跑安装时会选到与既有配置不同的那套布局，进而：
  #   1) 在另一个目录写出竞争用的 server.conf；
  #   2) 服务名与配置路径配对不上（openvpn-server@server 读的是
  #      /etc/openvpn/server/server.conf），启动失败；
  #   3) 更糟的是两套 unit 都被 enable，开机互相抢同一个端口。
  # 实测踩过：一台本来正常的机器，重跑安装后 VPN 起不来 + 重启循环。
  local classic_conf="${OVPN_DIR}/${SERVER_CN}.conf"
  local server_conf="${SERVER_DIR}/${SERVER_CN}.conf"
  local use_classic
  if [ -f "$classic_conf" ] && [ ! -f "$server_conf" ]; then
    use_classic=1
  elif [ -f "$server_conf" ] && [ ! -f "$classic_conf" ]; then
    use_classic=0
  elif [ -f "$classic_conf" ]; then
    # 两套同时存在：沿用非 server 目录那一套（本脚本历史的默认），并明确告警，
    # 不擅自删除任何一侧的配置。
    use_classic=1
    warn "检测到两套 OpenVPN 配置并存：${classic_conf} 与 ${server_conf}"
    warn "已沿用前者；请手动清理另一套，否则两个 unit 会抢同一个端口"
  elif systemctl list-unit-files 2>/dev/null | grep -q '^openvpn-server@'; then
    use_classic=0
  else
    use_classic=1
  fi
  if [ "$use_classic" -eq 1 ]; then
    OVPN_SERVICE="openvpn@${SERVER_CN}"
    SERVER_CONF="$classic_conf"
    EASYRSA_DIR="${OVPN_DIR}/easy-rsa"
    CCD_DIR="${OVPN_DIR}/ccd"
    CLIENT_EXPORT_DIR="${OVPN_DIR}/client"
  else
    OVPN_SERVICE="openvpn-server@${SERVER_CN}"
    SERVER_CONF="$server_conf"
    EASYRSA_DIR="${SERVER_DIR}/easy-rsa"
    CCD_DIR="${SERVER_DIR}/ccd"
    CLIENT_EXPORT_DIR="${SERVER_DIR}/client"
  fi
  CRL_PATH="${SERVER_DIR}/crl.pem"
  TA_KEY="${SERVER_DIR}/ta.key"
  info "OpenVPN 服务：${OVPN_SERVICE}"
  info "服务端配置：${SERVER_CONF}"
}

# ---------------------------------------------------------------------------
# 证书体系
# ---------------------------------------------------------------------------
EASYRSA_BIN=""

find_easyrsa() {
  local candidate
  candidate="$(find /usr/share/easy-rsa /usr/local/share/easy-rsa -maxdepth 3 -type f -name easyrsa 2>/dev/null | head -1 || true)"
  [ -n "$candidate" ] || die "找不到 easyrsa（请先安装 easy-rsa 包）"
  printf '%s' "$candidate"
}

init_pki() {
  step "初始化证书体系（EasyRSA）"
  mkdir -p "$LOG_DIR" "$SERVER_DIR" "$CCD_DIR" "$CLIENT_EXPORT_DIR"
  chmod 0755 "$LOG_DIR"

  # OpenVPN 以 user nobody 运行，而 status / ipp.txt 都写在 $LOG_DIR：
  # 若目录仍归 root，OpenVPN 降权后无法更新状态文件，面板的在线列表就会失效。
  # 目录给 0755、文件默认 0644，面板（非 root）只需读权限即可。
  if id -u "$OVPN_USER" >/dev/null 2>&1; then
    chown "${OVPN_USER}:${OVPN_GROUP}" "$LOG_DIR" 2>/dev/null \
      || warn "无法把 $LOG_DIR 归属给 ${OVPN_USER}:${OVPN_GROUP}，OpenVPN 状态输出可能不可用"
    chmod 0755 "$LOG_DIR"
  else
    warn "找不到用户 ${OVPN_USER}，server.conf 里的 user 指令可能需要手工调整"
  fi

  # PKI 分步补齐：这样"上次装到一半失败"的机器重跑时能接上，而不是被
  # "PKI 已存在"整段跳过、接着在下面 install 证书时报缺文件。
  if [ -d "${EASYRSA_DIR}/pki" ] && [ -f "${EASYRSA_DIR}/pki/ca.crt" ]; then
    ok "PKI 已存在（${EASYRSA_DIR}/pki），跳过 CA 初始化"
  else
    local src; src="$(dirname "$(find_easyrsa)")"
    [ -d "$EASYRSA_DIR" ] && [ -n "$(ls -A "$EASYRSA_DIR" 2>/dev/null || true)" ] \
      && warn "$EASYRSA_DIR 已存在但缺少 pki/，就地初始化（不覆盖已有文件）" \
      || { mkdir -p "$EASYRSA_DIR"; cp -a "${src}/." "$EASYRSA_DIR/"; }
    (
      cd "$EASYRSA_DIR" || die "无法进入 ${EASYRSA_DIR}"
      export EASYRSA_BATCH=1 EASYRSA_ALGO=rsa EASYRSA_KEY_SIZE=2048
      ./easyrsa init-pki >/dev/null
      info "生成 CA（无口令）…"
      # EASYRSA_REQ_CN 只对 build-ca 有意义，**绝不能 export**：
      # EasyRSA 3.1+（Debian 12 自带的正是 3.1.0）里 build-server-full /
      # build-client-full 的 CN 取自名字参数，若环境里还留着外部 CN，会直接报
      #   Option conflict: 'build-server-full' does not support setting an external commonName
      # 3.0.x 容忍这种写法，所以这个坑只在较新的发行版上暴露。
      EASYRSA_REQ_CN="ovpn-oneclick-ca" ./easyrsa build-ca nopass >/dev/null
    )
    ok "CA 初始化完成"
  fi

  # 服务端证书与 CRL 独立判断：任一步缺失都补上（中断重跑不会卡住）
  if [ ! -s "${EASYRSA_DIR}/pki/issued/${SERVER_CN}.crt" ] \
     || [ ! -s "${EASYRSA_DIR}/pki/private/${SERVER_CN}.key" ]; then
    info "生成服务端证书（${SERVER_CN}）…"
    ( cd "$EASYRSA_DIR" || die "无法进入 ${EASYRSA_DIR}"
      export EASYRSA_BATCH=1 EASYRSA_ALGO=rsa EASYRSA_KEY_SIZE=2048
      ./easyrsa build-server-full "$SERVER_CN" nopass >/dev/null ) \
      || die "生成服务端证书失败（EasyRSA 版本：$(easyrsa --version 2>/dev/null | head -1 || echo 未知)）"
  fi
  if [ ! -s "${EASYRSA_DIR}/pki/crl.pem" ]; then
    info "生成初始 CRL…"
    ( cd "$EASYRSA_DIR" && EASYRSA_BATCH=1 ./easyrsa gen-crl >/dev/null ) || die "生成 CRL 失败"
  fi

  # tls-crypt 密钥
  if [ ! -s "$TA_KEY" ]; then
    info "生成 tls-crypt 密钥…"
    openvpn --genkey secret "$TA_KEY"
    chmod 0600 "$TA_KEY"
  fi

  # DH 参数（用 -dsaparam 快速生成，与 gen-dh 等效可用但快得多）
  if [ ! -s "${SERVER_DIR}/dh.pem" ]; then
    info "生成 DH 参数（约 10~30 秒）…"
    openssl dhparam -dsaparam -out "${SERVER_DIR}/dh.pem" 2048 2>/dev/null
  fi

  place_file 0644 "${EASYRSA_DIR}/pki/ca.crt"                   "${SERVER_DIR}/ca.crt"
  place_file 0644 "${EASYRSA_DIR}/pki/issued/${SERVER_CN}.crt"  "${SERVER_DIR}/${SERVER_CN}.crt"
  place_file 0600 "${EASYRSA_DIR}/pki/private/${SERVER_CN}.key" "${SERVER_DIR}/${SERVER_CN}.key"
  refresh_crl
}

# 把文件放到目标位置；**源与目标相同时只修权限**。
# GNU install 在 src == dst 时会以 "are the same file" 报错退出，而 --adopt 采纳
# 既有安装时这很常见：CRL_PATH 会被对齐到对方实际使用的位置，而那个位置常常就是
# EasyRSA 自己的 pki/crl.pem。实测这个错误会直接中断整个安装。
place_file() { # place_file <权限> <源> <目标>
  local mode="$1" src="$2" dst="$3"
  if [ "$(readlink -f "$src" 2>/dev/null)" = "$(readlink -f "$dst" 2>/dev/null)" ]; then
    chmod "$mode" "$dst" 2>/dev/null || true
    return 0
  fi
  install -m "$mode" "$src" "$dst"
}

refresh_crl() {
  local src="${EASYRSA_DIR}/pki/crl.pem"
  [ -f "$src" ] || return 0
  # CRL 必须对 OpenVPN 降权后的 nobody 可读（0644），否则吊销不会生效
  place_file 0644 "$src" "$CRL_PATH"
}

# ---------------------------------------------------------------------------
# 服务端配置
# ---------------------------------------------------------------------------
render() { # render <src> <dst> KEY VALUE [KEY VALUE ...]
  local src="$1" dst="$2"; shift 2
  [ -f "$src" ] || die "模板不存在：$src"
  mkdir -p "$(dirname "$dst")"
  cp -f "$src" "$dst"
  chmod 0644 "$dst"
  while [ "$#" -ge 2 ]; do
    local key="$1" val="$2"; shift 2
    local esc; esc="$(printf '%s' "$val" | sed -e 's/[&|\\]/\\&/g')"
    sed -i "s|__${key}__|${esc}|g" "$dst"
  done
}

build_push_routes() {
  local out=""
  if [ "$FULL_TUNNEL" = "1" ]; then
    out='push "redirect-gateway def1 bypass-dhcp"'
  else
    for r in "${EXTRA_ROUTES[@]:-}"; do
      [ -n "$r" ] || continue
      if printf '%s' "$r" | grep -q '/'; then
        # CIDR -> network + mask
        local net mask
        net="$(printf '%s' "$r" | cut -d/ -f1)"
        local bits; bits="$(printf '%s' "$r" | cut -d/ -f2)"
        mask="$(cidr_to_mask "$bits")"
        out+="push \"route ${net} ${mask}\""$'\n'
      else
        out+="push \"route ${r}\""$'\n'
      fi
    done
  fi
  printf '%s' "$out"
}

cidr_to_mask() {
  local bits="$1" mask="" i
  for i in 1 2 3 4; do
    if [ "$bits" -ge 8 ]; then mask+="255"; bits=$((bits-8))
    elif [ "$bits" -le 0 ]; then mask+="0"
    else mask+=$((256 - 2**(8-bits))); bits=0; fi
    [ "$i" -lt 4 ] && mask+="."
  done
  printf '%s' "$mask"
}

# 反向换算：255.255.255.0 -> 24（采纳既有安装时要读对方的 server 行）
mask_to_cidr() {
  local mask="$1" bits=0 n
  local IFS=.
  for n in $mask; do
    case "$n" in
      255) bits=$((bits + 8)) ;;
      254) bits=$((bits + 7)) ;;
      252) bits=$((bits + 6)) ;;
      248) bits=$((bits + 5)) ;;
      240) bits=$((bits + 4)) ;;
      224) bits=$((bits + 3)) ;;
      192) bits=$((bits + 2)) ;;
      128) bits=$((bits + 1)) ;;
      0)   ;;
      *)   return 1 ;;
    esac
  done
  printf '%s' "$bits"
}

# --block-ipv6 需要 OpenVPN 2.4+。如果不提前检查，低版本会在**服务端加载配置时**
# 才失败（systemd 起不来），排查成本高得多，所以这里在写配置前就拦住。
require_openvpn_for_ipv6() {
  [ "$BLOCK_IPV6" = "1" ] || return 0
  local ver major minor
  ver="$(openvpn --version 2>/dev/null | head -1 | awk '{print $2}')" || ver=""
  case "$ver" in
    [0-9]*.[0-9]*) ;;
    *)
      warn "无法确定 OpenVPN 版本；--block-ipv6 需要 2.4+，若服务起不来请去掉该参数"
      return 0
      ;;
  esac
  major="${ver%%.*}"
  minor="${ver#*.}"
  minor="${minor%%.*}"
  if [ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 4 ]; }; then
    return 0
  fi
  die "--block-ipv6 需要 OpenVPN 2.4 及以上，当前是 ${ver}。
    请去掉 --block-ipv6，或先升级 OpenVPN 后重试。"
}

write_server_conf() {
  step "写入 OpenVPN 服务端配置"

  # --adopt：只在**确实存在**既有配置时打补丁。不能无条件走这条路：
  # 只有 PKI 而没有 server.conf 的机器（典型场景是上次安装中断留下的现场）
  # 打补丁无处可打，会变成"什么都没写"然后服务必然起不来——而这里是写配置的
  # 唯一入口，所以必须先判断文件在不在。
  # 另外这里**不再用 `|| true` 吞掉失败**：往既有配置里注入面板指令失败时，
  # 结果是一份不完整、起不来的配置，应当立刻报错而不是继续往下走。
  if [ "${ADOPTED:-0}" = "1" ]; then
    if [ -s "$SERVER_CONF" ]; then
      inject_panel_directives
      return 0
    fi
    warn "${SERVER_CONF} 不存在（只有 PKI），无法在既有配置上打补丁，改为写入一份新配置"
  fi

  if [ -f "$SERVER_CONF" ]; then
    cp -a "$SERVER_CONF" "${SERVER_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
    warn "$SERVER_CONF 已存在，已备份后覆盖"
  fi

  local push_routes; push_routes="$(build_push_routes)"

  # DNS：全局代理时推公共 DNS 没问题；分流模式下推公共 DNS 会把内网域名也解析到
  # 公网，等于把内网访问搞坏，所以"分流且未显式指定"时一律不推 DNS。
  local dns_lines=""
  if [ "$FULL_TUNNEL" = "1" ] || [ "$DNS_EXPLICIT" = "1" ]; then
    dns_lines="push \"dhcp-option DNS ${DNS1}\""
    if [ -n "$DNS2" ]; then
      dns_lines="${dns_lines}"$'\n'"push \"dhcp-option DNS ${DNS2}\""
    fi
  else
    warn "分流模式且未显式指定 DNS：本次不推送 DNS"
    warn "内网若需要域名解析，请加 --dns1 <内网DNS地址>"
  fi

  # IPv6 泄漏防护（可选）。按 OpenVPN 手册 --block-ipv6 一节给出的做法：
  # 推一份"看着合法"的 IPv6 配置并把客户端 IPv6 默认路由拉进隧道，
  # 再由服务端的 block-ipv6 把收到的 IPv6 包回以 no-route，从真正屏蔽掉。
  local ipv6_lines=""
  if [ "$BLOCK_IPV6" = "1" ]; then
    ipv6_lines='push "ifconfig-ipv6 fd15:53b6:dead::2/64 fd15:53b6:dead::1"'$'\n'
    ipv6_lines+='push "redirect-gateway ipv6"'$'\n'
    ipv6_lines+='block-ipv6'
  fi

  # 先算好变量再写进 heredoc：不要在这里用 $( [ ... ] && echo ... )，
  # 条件为假时命令替换返回非 0，容易与 set -e 相互作用出意外。
  # explicit-exit-notify 与 fast-io 都只对 UDP 有效，TCP 下必须不写。
  local fast_io="" exit_notify=""
  if [ "$VPN_PROTO" = "udp" ]; then
    fast_io="fast-io"
    exit_notify="explicit-exit-notify 1"
  fi

  # 配置分成两部分：
  #   ① 固定部分——面板不碰（证书、management/status、ccd、crl、日志等）
  #   ② 末尾的 ovpn-oneclick managed 块——面板「服务端设置」页面维护的内容，
  #      由特权助手 apply-config 校验后落地，且只允许改这个块里的指令。
  # 这样"面板能改配置"和"面板不能塞任意指令进 root 读的配置"能同时成立。
  cat > "$SERVER_CONF" <<EOF
# ===== 由 ovpn-oneclick ${SCRIPT_VERSION} 生成 =====
# 固定部分：面板不会修改下面这些行。
dev tun

ca ${SERVER_DIR}/ca.crt
cert ${SERVER_DIR}/${SERVER_CN}.crt
key ${SERVER_DIR}/${SERVER_CN}.key
dh ${SERVER_DIR}/dh.pem
tls-crypt ${TA_KEY}

# data-ciphers 与 auth 属于下面的托管块（面板可改），这里不再重复写：
# 否则同名指令会并存（后者胜出），造成「面板显示 A、实际生效 B」。
data-ciphers-fallback AES-256-GCM

ifconfig-pool-persist ${LOG_DIR}/ipp.txt
txqueuelen 1000
persist-key
persist-tun

# ---- 证书吊销 ----
# 只要该文件内容变化（mtime/size 改变），OpenVPN 就会重新读取它，因此吊销后
# 无需重启服务。注意两点：① 重新读取发生在降权到 nobody **之后**，所以该文件
# 必须对 nobody 可读（本脚本与面板都会保证 0644）；② 已经建立的会话不会被
# 立即切断，要用 management 的 kill 或等它自行重连。
crl-verify ${CRL_PATH}

# ---- 面板依赖的管理接口与状态输出 ----
management 127.0.0.1 ${MGMT_PORT}
status ${LOG_DIR}/openvpn-status.log
status-version 3

# ---- 固定 IP / 每客户端路由 ----
client-config-dir ${CCD_DIR}

# user/group 属于下方的托管块（面板可改），这里不再重复写。
# 之前这里把 user 写死成 nobody，而 OVPN_USER 在 nobody 不存在时会退成 root，
# 两处不一致会让「面板显示的用户」与「实际降权的用户」不同。

log-append ${LOG_DIR}/openvpn.log
verb 3

# >>> ovpn-oneclick managed (do not edit by hand) >>>
# 以下设置可在面板的「服务端设置」页面里修改。
# 面板只写这个块；助手 apply-config 会校验取值、备份、原子替换，
# 并在服务起不来时自动回滚。请勿手工编辑这个块（会被面板覆盖）。
port ${VPN_PORT}
proto ${VPN_PROTO}
server ${VPN_NETWORK} ${VPN_NETMASK}
topology subnet
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
user ${OVPN_USER}
group ${OVPN_GROUP}
${dns_lines}
${push_routes}
${ipv6_lines}
sndbuf 393216
rcvbuf 393216
push "sndbuf 393216"
push "rcvbuf 393216"
mssfix 1420
keepalive 10 120
client-to-client
${exit_notify}
${fast_io}
# <<< ovpn-oneclick managed <<<
EOF
  chmod 0644 "$SERVER_CONF"
  ok "已写入 ${SERVER_CONF}（proto=${VPN_PROTO} port=${VPN_PORT} 网段=${VPN_NETWORK}/${VPN_CIDR}）"
}

# ---------------------------------------------------------------------------
# 内核转发与 NAT
# ---------------------------------------------------------------------------
EGRESS_IF=""

detect_egress_if() {
  EGRESS_IF="$(ip -4 route ls default 2>/dev/null | grep -oP '(?<=dev )[^ ]+' | head -1 || true)"
  [ -n "$EGRESS_IF" ] || EGRESS_IF="$(ip route get 8.8.8.8 2>/dev/null | grep -oP '(?<=dev )[^ ]+' | head -1 || true)"
  [ -n "$EGRESS_IF" ] || die "无法确定默认出口网卡"
  info "默认出口网卡：${EGRESS_IF}"
}

enable_ip_forward() {
  step "开启内核转发"
  cat > /etc/sysctl.d/99-ovpn-oneclick.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
  sysctl -q --system 2>/dev/null || sysctl -qw net.ipv4.ip_forward=1
  [ "$(cat /proc/sys/net/ipv4/ip_forward)" = "1" ] || die "开启 ip_forward 失败"
  ok "net.ipv4.ip_forward=1"
}

setup_nat() {
  [ "$FULL_TUNNEL" = "1" ] || { info "分流模式：跳过 NAT 配置"; return 0; }
  step "配置 NAT 转发（全局代理模式）"
  detect_egress_if

  local net="${VPN_NETWORK}/${VPN_CIDR}"

  if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    info "检测到 firewalld，使用 firewalld 配置"
    firewall-cmd --permanent --add-masquerade >/dev/null
    firewall-cmd --permanent --add-port="${VPN_PORT}/${VPN_PROTO}" >/dev/null
    firewall-cmd --permanent --add-port="${PANEL_WEB_PORT}/tcp" >/dev/null 2>&1 || true
    firewall-cmd --permanent --remove-service=http >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null
    ok "firewalld：已开启 masquerade 并放行 ${VPN_PORT}/${VPN_PROTO}"
  else
    info "使用 iptables 配置 NAT（并通过 systemd 单元持久化）"
    mkdir -p /usr/local/lib/ovpn-oneclick
    cat > /usr/local/lib/ovpn-oneclick/nat.sh <<EOF
#!/bin/sh
# 由 ovpn-oneclick 生成：幂等地重建 NAT 规则
set -e
NET="${net}"
IF="${EGRESS_IF}"
TUN="tun0"
iptables -t nat -C POSTROUTING -s "\$NET" -o "\$IF" -j MASQUERADE 2>/dev/null || \\
  iptables -t nat -A POSTROUTING -s "\$NET" -o "\$IF" -j MASQUERADE
iptables -C FORWARD -i "\$TUN" -o "\$IF" -j ACCEPT 2>/dev/null || \\
  iptables -A FORWARD -i "\$TUN" -o "\$IF" -j ACCEPT
iptables -C FORWARD -i "\$IF" -o "\$TUN" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \\
  iptables -A FORWARD -i "\$IF" -o "\$TUN" -m state --state RELATED,ESTABLISHED -j ACCEPT
# 放行面板与 OpenVPN 端口（INPUT）
iptables -C INPUT -p ${VPN_PROTO} --dport ${VPN_PORT} -j ACCEPT 2>/dev/null || \\
  iptables -A INPUT -p ${VPN_PROTO} --dport ${VPN_PORT} -j ACCEPT
exit 0
EOF
    chmod 0755 /usr/local/lib/ovpn-oneclick/nat.sh
    /usr/local/lib/ovpn-oneclick/nat.sh
    cat > /etc/systemd/system/ovpn-oneclick-nat.service <<'EOF'
[Unit]
Description=ovpn-oneclick NAT rules for OpenVPN
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/lib/ovpn-oneclick/nat.sh

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable ovpn-oneclick-nat >/dev/null 2>&1 || true
    ok "iptables NAT 规则已生效并持久化（ovpn-oneclick-nat.service）"
  fi

  # 关闭可能存在的 ufw 对转发的拦截
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "检测到 ufw 处于启用状态：已放行 ${VPN_PORT}/${VPN_PROTO}"
    ufw allow "${VPN_PORT}/${VPN_PROTO}" >/dev/null 2>&1 || true
    if ! grep -q 'DEFAULT_FORWARD_POLICY="ACCEPT"' /etc/default/ufw 2>/dev/null; then
      warn "ufw 的 DEFAULT_FORWARD_POLICY 不是 ACCEPT，若客户端无法上网请改为 ACCEPT 后 ufw reload"
    fi
  fi
}

# ---------------------------------------------------------------------------
# 启动 OpenVPN
# ---------------------------------------------------------------------------
start_openvpn() {
  step "启动 OpenVPN 服务"
  systemctl enable "$OVPN_SERVICE" >/dev/null 2>&1 || true
  systemctl restart "$OVPN_SERVICE"
  sleep 2
  local state; state="$(systemctl is-active "$OVPN_SERVICE" 2>&1 || true)"
  if [ "$state" != "active" ]; then
    journalctl -u "$OVPN_SERVICE" -n 30 --no-pager 2>/dev/null | tail -30 >&2 || true
    die "OpenVPN 启动失败（${state}），请查看：journalctl -u ${OVPN_SERVICE}"
  fi
  ok "OpenVPN 运行中（${OVPN_SERVICE}）"
}

# ---------------------------------------------------------------------------
# 客户端管理
# ---------------------------------------------------------------------------
ovpn_tls_block() {
  # 与服务端保持一致：tls-crypt 优先
  if grep -qE '^\s*tls-crypt' "$SERVER_CONF" 2>/dev/null; then
    printf '\n<tls-crypt>\n%s\n</tls-crypt>\n' "$(cat "$TA_KEY")"
  else
    printf '\nkey-direction 1\n<tls-auth>\n%s\n</tls-auth>\n' "$(cat "$TA_KEY")"
  fi
}

write_client_ovpn() { # write_client_ovpn <名称>
  local name="$1"
  local out="${CLIENT_EXPORT_DIR}/${name}.ovpn"
  local pki="${EASYRSA_DIR}/pki"
  local ca="${SERVER_DIR}/ca.crt"
  local crt="${pki}/issued/${name}.crt"
  local key="${pki}/private/${name}.key"
  [ -f "$crt" ] || die "找不到客户端证书：${crt}"
  [ -f "$key" ] || die "找不到客户端私钥：${key}"

  local proto port cipher data_ciphers auth
  proto="$(grep -oP '^\s*proto\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  port="$(grep -oP '^\s*port\s+\K\S+'  "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  cipher="$(grep -oP '^\s*cipher\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  data_ciphers="$(grep -oP '^\s*data-ciphers\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  auth="$(grep -oP '^\s*auth\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  : "${proto:=udp}" "${port:=1194}" "${auth:=SHA256}"

  mkdir -p "$CLIENT_EXPORT_DIR"
  {
    echo "client"
    echo "dev tun"
    echo "proto ${proto}"
    echo "remote ${SERVER_ADDR} ${port}"
    echo "resolv-retry infinite"
    echo "nobind"
    echo "persist-key"
    echo "persist-tun"
    echo "remote-cert-tls server"
    echo "verb 3"
    # 注意：这里用 if 而不是 "[ ... ] && echo"，因为脚本开了 set -e，
    # 条件为假时 `[ ]` 返回非 0 会让整个脚本直接退出。
    if [ "$proto" = "udp" ]; then echo "explicit-exit-notify 1"; fi
    # 客户端必须显式带上 data-ciphers，且要与服务端一致。
    # 原来这里是 `if [ -n "$data_ciphers" ]`：一旦 server.conf 里没有这一行（例如
    # 面板把它改成托管项、旧行被 retire 成注释之后），客户端就会**整行省略**，
    # 于是用 OpenVPN 自己的默认套件去协商，可能与服务端对不上而握手失败。
    # 这里的默认值与 install.sh 写入托管块时的默认值、以及 OpenVPN 2.6 的内建默认
    # 完全一致，所以即便 server.conf 暂时缺这一行也不会造成两端不一致。
    echo "data-ciphers ${data_ciphers:-AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305}"
    if [ -n "$cipher" ]; then echo "cipher ${cipher}"; fi
    echo "auth ${auth}"
    printf '\n<ca>\n%s\n</ca>\n' "$(cat "$ca")"
    printf '<cert>\n%s\n</cert>\n' "$(cat "$crt")"
    printf '<key>\n%s\n</key>\n' "$(cat "$key")"
    ovpn_tls_block
  } > "$out"
  chmod 0600 "$out"
  printf '%s' "$out"
}

add_client() {
  local name="$1"
  resolve_server_addr
  local pki="${EASYRSA_DIR}/pki"
  if [ -f "${pki}/issued/${name}.crt" ]; then
    warn "客户端 ${name} 已存在，直接导出配置"
  else
    info "为 ${name} 签发证书…"
    ( cd "$EASYRSA_DIR" && EASYRSA_BATCH=1 ./easyrsa build-client-full "$name" nopass >/dev/null )
  fi
  local out; out="$(write_client_ovpn "$name")"
  ok "客户端配置：${out}"
}

# 通过 management 接口踢掉某个客户端的活动会话（只影响该客户端）。
# 用 bash 的 /dev/tcp，不引入额外依赖。
#
# 已知限制：OpenVPN 的 management 端口同一时刻只服务一个客户端连接。如果面板
# 正在保持长连接，这里会被拒绝（踢不掉）。那种情况请用面板里的强制下线。
kick_session() {
  local name="$1" resp=""
  if ! exec 3<>"/dev/tcp/127.0.0.1/${MGMT_PORT}" 2>/dev/null; then
    return 1
  fi
  head -c 256 <&3 >/dev/null 2>&1 || true
  printf 'kill %s\n' "$name" >&3 2>/dev/null || true
  sleep 1
  resp="$(head -c 256 <&3 2>/dev/null || true)"
  exec 3<&- 3>&- 2>/dev/null || true
  case "$resp" in
    *SUCCESS*) return 0 ;;
    *)         return 1 ;;
  esac
}

revoke_client() {
  local name="$1"
  local pki="${EASYRSA_DIR}/pki"
  [ -f "${pki}/issued/${name}.crt" ] || die "客户端 ${name} 不存在"
  if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "确认吊销客户端 ${name}？[y/N] " ans
    case "$ans" in [yY]*) ;; *) die "已取消" ;; esac
  fi
  ( cd "$EASYRSA_DIR" && EASYRSA_BATCH=1 ./easyrsa revoke "$name" >/dev/null && EASYRSA_BATCH=1 ./easyrsa gen-crl >/dev/null )
  refresh_crl
  rm -f "${CLIENT_EXPORT_DIR}/${name}.ovpn"
  if kick_session "$name"; then
    ok "已吊销 ${name} 并刷新 CRL：其活动会话已断开，重连会被拒绝"
  else
    ok "已吊销 ${name} 并刷新 CRL：新连接立即被拒绝，其他在线用户不受影响"
    info "未能确认断开其现有会话（management 端口可能正被面板占用，或该客户端当时不在线）"
    info "需要立刻断开时，可在面板里对该客户端执行「强制下线」"
  fi
}

list_clients() {
  local pki="${EASYRSA_DIR}/pki"
  [ -d "${pki}/issued" ] || { info "暂无客户端"; return 0; }
  local found=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    local name; name="$(basename "$f" .crt)"
    [ "$name" = "$SERVER_CN" ] && continue
    local revoked=""
    if grep -q "^R\s.*CN=${name}\b" "${pki}/index.txt" 2>/dev/null; then
      revoked="  [已吊销]"
    fi
    printf '  %s%s\n' "$name" "$revoked"
    found=1
  done < <(find "${pki}/issued" -maxdepth 1 -name '*.crt' 2>/dev/null | sort)
  [ "$found" = "1" ] || info "暂无客户端"
}

export_client() {
  local name="$1"
  resolve_server_addr
  local out; out="$(write_client_ovpn "$name")"
  ok "已导出：${out}"
}

# ---------------------------------------------------------------------------
# 面板部署
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR=""
EXTRACT_DIR=""

cleanup() {
  [ -n "$EXTRACT_DIR" ] && rm -rf "$EXTRACT_DIR" || true
  [ -n "$WORK_DIR" ] && rm -rf "$WORK_DIR" || true
  return 0
}
trap 'cleanup' EXIT

fetch_panel() {
  step "获取面板程序"
  local local_asset="${SCRIPT_DIR}/${ASSET_PATH}"
  if [ -f "$local_asset" ]; then
    ok "使用脚本同级目录中的面板包：${local_asset}"
    printf '%s' "$local_asset"
    return 0
  fi

  WORK_DIR="$(mktemp -d)"
  local dst="${WORK_DIR}/panel.tar.gz"
  info "下载 ${PANEL_TARBALL_URL}"
  if ! curl -fL --connect-timeout 15 --retry 2 -o "$dst" "$PANEL_TARBALL_URL" 2>/dev/null; then
    die "面板包下载失败：${PANEL_TARBALL_URL}

    常见原因：
      1. 仓库是私有的 —— Gitee 对私有仓库的 /raw/ 路径一律返回 403，连带上令牌
         也不行（只有 api/v5 的 contents 接口可用）；GitHub 的私有仓库同样取不到
         匿名 raw。另外 Gitee 会以「内容涉及代理」为由强制把 OpenVPN 类仓库设为私有，
         所以这类项目更适合放在 GitHub。
      2. 网络不通，或 REPO_HOST / REPO_OWNER / REPO_NAME / REPO_BRANCH 与实际不符。

    三种可行做法（任选其一）：
      a) 把 install.sh 与面板包放到你自己的服务器/对象存储上，然后覆盖下载源：
           PANEL_TARBALL_URL=https://你的域名/ovpn/ovpnmanager-panel.tar.gz \\
           REMOTE_VERSION_URL=https://你的域名/ovpn/VERSION.txt \\
           SELF_URL=https://你的域名/ovpn/install.sh \\
           bash install.sh --auto
      b) 手动下载面板包，放到本脚本同级目录后重跑（无需联网取包）：
           ${SCRIPT_DIR}/${ASSET_PATH}
      c) 换一个允许公开托管此类内容的平台（GitHub / 自建 Gitea 等），
         用 REPO_OWNER / REPO_NAME 覆盖即可。"
  fi
  tar -tzf "$dst" >/dev/null 2>&1 || die "面板包损坏（不是有效的 tar.gz）"
  printf '%s' "$dst"
}

deploy_panel() {
  [ "$PANEL_ENABLE" = "1" ] || { warn "--no-panel：跳过面板部署"; return 0; }
  step "部署管理面板到 ${PANEL_HOME}"

  local asset; asset="$(fetch_panel)"
  local extract; extract="$(mktemp -d)"
  EXTRACT_DIR="$extract"
  tar -xzf "$asset" -C "$extract"
  local src
  src="$(find "$extract" -maxdepth 2 -type d -name backend | head -1)"
  [ -n "$src" ] || die "面板包结构异常：找不到 backend/ 目录"
  src="$(dirname "$src")"

  # 系统用户
  if ! getent group "$PANEL_USER" >/dev/null 2>&1; then groupadd --system "$PANEL_USER"; fi
  if ! id -u "$PANEL_USER" >/dev/null 2>&1; then
    local shell="/usr/sbin/nologin"; [ -x "$shell" ] || shell="/sbin/nologin"
    useradd --system --gid "$PANEL_USER" --home-dir "$PANEL_HOME" --shell "$shell" "$PANEL_USER"
  fi

  mkdir -p "$PANEL_HOME" "${PANEL_HOME}/logs"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '.env' --exclude 'data/' --exclude '.venv/' \
      "${src}/backend/" "${PANEL_HOME}/backend/"
  else
    mkdir -p "${PANEL_HOME}/backend"
    find "${PANEL_HOME}/backend" -mindepth 1 -maxdepth 1 ! -name '.env' ! -name 'data' ! -name '.venv' -exec rm -rf {} +
    cp -a "${src}/backend/." "${PANEL_HOME}/backend/"
  fi
  mkdir -p "${PANEL_HOME}/backend/data"
  TPL_DIR="${src}/templates"

  # 面板访问设置的校验库与 nginx 渲染库：安装时直接用仓库里的这两份实现，
  # 保证"安装脚本渲染"和"root 助手渲染"是同一段代码（见 panel-nginx.sh）。
  # shellcheck disable=SC1090
  . "${TPL_DIR}/panel-validate.sh"
  # shellcheck disable=SC1090
  . "${TPL_DIR}/panel-nginx.sh"
  # **优先读 panel.state**：重装/升级不冲掉用户在面板里改过的端口/域名/TLS
  load_panel_state

  # 虚拟环境
  if [ ! -x "${PANEL_HOME}/backend/.venv/bin/python" ]; then
    info "创建 Python 虚拟环境…"
    python3 -m venv "${PANEL_HOME}/backend/.venv"
  fi
  # pip 源：默认走国内镜像，失败自动退回官方 PyPI。
  # 原因：PyPI 的包实际托管在 files.pythonhosted.org，国内服务器上实测会
  # ReadTimeout 直接把安装打断（apt 也是同类问题，已换镜像）。国内镜像快两个数量级。
  # 非国内环境或想用自建源时覆盖即可：
  #   PIP_INDEX_URL=https://pypi.org/simple/ bash install.sh --auto
  local pip_bin="${PANEL_HOME}/backend/.venv/bin/pip"
  local pip_index="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
  local pip_opts=(--timeout 60 --retries 5 --disable-pip-version-check)
  info "安装面板依赖（pip，源：${pip_index}）…"
  if ! "$pip_bin" install "${pip_opts[@]}" --index-url "$pip_index" --upgrade pip -q \
     || ! "$pip_bin" install "${pip_opts[@]}" --index-url "$pip_index" \
            -r "${PANEL_HOME}/backend/requirements.txt" -q; then
    warn "使用 ${pip_index} 安装失败，改用官方 PyPI 重试…"
    "$pip_bin" install "${pip_opts[@]}" --upgrade pip -q
    "$pip_bin" install "${pip_opts[@]}" -r "${PANEL_HOME}/backend/requirements.txt" -q
  fi

  # 前端静态文件
  mkdir -p "${WEB_ROOT}/${WEB_NAME}"
  rm -rf "${WEB_ROOT:?}/${WEB_NAME:?}"/*
  cp -a "${src}/frontend/." "${WEB_ROOT}/${WEB_NAME}/"
  chmod -R a+rX "${WEB_ROOT}/${WEB_NAME}"

  # .env：升级或重装时**绝不**重新生成 SECRET_KEY 与管理员口令，
  # 只把新版本模板里新增、而现有 .env 没有的键补上（见 merge_env）。
  local secret admin_pw env_rendered
  secret="$(openssl rand -hex 32)"
  # 用 hex 而不是 base64 过滤：避免 head 提前关管道在 set -o pipefail 下引发 141
  admin_pw="$(openssl rand -hex 12)"
  env_rendered="$(mktemp)"
  render "${TPL_DIR}/env.template" "$env_rendered" \
    PANEL_HOME "$PANEL_HOME" \
    SECRET_KEY "$secret" \
    ADMIN_PASSWORD "$admin_pw" \
    OVPN_SERVICE "$OVPN_SERVICE" \
    SYSTEMCTL_BIN "/usr/local/lib/ovpnmanager/systemctl-wrapper" \
    SERVER_CONF_PATH "$SERVER_CONF" \
    EASYRSA_PATH "$EASYRSA_DIR" \
    CCD_PATH "$CCD_DIR" \
    TA_KEY_PATH "$TA_KEY" \
    CRL_PATH "$CRL_PATH" \
    STATUS_PATH "${LOG_DIR}/openvpn-status.log" \
    EXPORT_PATH "$CLIENT_EXPORT_DIR" \
    MGMT_PORT "$MGMT_PORT" \
    PANEL_PORT "$PANEL_PORT" \
    PUBLIC_IP "$SERVER_ADDR" \
    VPN_PORT "$VPN_PORT"

  if [ -e "${PANEL_HOME}/backend/.env" ]; then
    warn "${PANEL_HOME}/backend/.env 已存在：保留原有 SECRET_KEY 与管理员口令"
    merge_env "$env_rendered" "${PANEL_HOME}/backend/.env"
    chmod 0600 "${PANEL_HOME}/backend/.env"
  else
    install -m 0600 "$env_rendered" "${PANEL_HOME}/backend/.env"
    printf '\n'
    printf "${C_WARN}================= 请立即保存面板登录信息 =================${C_RESET}\n"
    printf "  面板地址 : %s://<服务器地址>:%s/%s/\n" "$PANEL_SCHEME" "$PANEL_WEB_PORT" "$WEB_NAME"
    printf "  用户名   : admin\n"
    printf "  初始口令 : %s\n" "$admin_pw"
    printf "${C_WARN}=========================================================${C_RESET}\n\n"
  fi
  rm -f "$env_rendered"

  # 降权：受限 systemctl 包装脚本 + sudoers 白名单
  install -D -m 0755 "${TPL_DIR}/systemctl-wrapper.sh" /usr/local/lib/ovpnmanager/systemctl-wrapper
  render "${TPL_DIR}/sudoers" "/etc/sudoers.d/ovpnmanager" \
    PANEL_USER "$PANEL_USER" OVPN_SERVICE "$OVPN_SERVICE" PANEL_SERVICE "ovpnmanager" \
    SYSTEMCTL "$(command -v systemctl)"
  chmod 0440 /etc/sudoers.d/ovpnmanager
  visudo -cf /etc/sudoers.d/ovpnmanager >/dev/null 2>&1 || { rm -f /etc/sudoers.d/ovpnmanager; die "sudoers 校验失败，已回滚"; }

  # 「服务端设置」页面的落地通道：特权助手 + 它的配置 + 校验库
  install_apply_helper
  # 「面板设置」页面的落地通道：第二个特权助手 + panel.conf + 校验/渲染库
  install_panel_helper

  # 归属与权限
  chown -R "${PANEL_USER}:${PANEL_USER}" "$PANEL_HOME"
  chmod 0600 "${PANEL_HOME}/backend/.env"
  chown -R "${PANEL_USER}:${PANEL_USER}" "$EASYRSA_DIR" "$CCD_DIR" "$CLIENT_EXPORT_DIR" 2>/dev/null || true
  touch "$CRL_PATH"; chown "${PANEL_USER}:${PANEL_USER}" "$CRL_PATH"; chmod 0644 "$CRL_PATH"
  touch "${LOG_DIR}/openvpn-status.log" 2>/dev/null || true

  # systemd
  render "${TPL_DIR}/ovpnmanager.service" "/etc/systemd/system/ovpnmanager.service" \
    PANEL_USER "$PANEL_USER" PANEL_HOME "$PANEL_HOME" PANEL_PORT "$PANEL_PORT" \
    OVPN_SERVICE "$OVPN_SERVICE"
  systemctl daemon-reload
  systemctl enable ovpnmanager >/dev/null 2>&1 || true

  # nginx（同源反代 /api，消除 CORS 预检；开启 gzip）
  #
  # 监听端口 / 绑定域名 / 放行来源 / TLS 由 render_panel_nginx 渲染，用的是
  # templates/panel-nginx.sh —— 与 root 助手 apply-panel-config **同一份实现**，
  # 并且值来自 load_panel_state（**优先读 /etc/ovpn-oneclick/panel.state**）。
  # 这条优先级是刻意设计的：否则一次 --update 就会把用户在面板里改过的端口/域名/TLS
  # 冲回默认值，而面板可能因此再也连不上（端口变了、安全组还没跟着开）。
  local conf="/etc/nginx/conf.d/ovpnmanager.conf"
  [ -d /etc/nginx/sites-available ] && conf="/etc/nginx/sites-available/ovpnmanager.conf"
  render_panel_nginx "$conf"
  info "面板入口：${PANEL_SCHEME}://${PANEL_SERVER_NAME}:${PANEL_WEB_PORT}/${WEB_NAME}/（放行 ${PANEL_ALLOW_MODE}，TLS ${PANEL_TLS_MODE}）"
  if [ "$conf" = "/etc/nginx/sites-available/ovpnmanager.conf" ]; then
    ln -sfn "$conf" /etc/nginx/sites-enabled/ovpnmanager.conf
    [ -e /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default
  else
    [ -e /etc/nginx/conf.d/default.conf ] && mv /etc/nginx/conf.d/default.conf "/etc/nginx/conf.d/default.conf.disabled.$(date +%s)"
  fi
  nginx -t >/dev/null 2>&1 || die "nginx 配置校验失败，请检查 $conf"
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl restart nginx

  # 把本次真正渲染用的值写进 panel.state：下次重装/升级从这里恢复（见 load_panel_state）
  write_panel_state
  ok "面板访问设置已记录到 ${PANEL_STATE_FILE}"

  # 日志轮转
  render "${TPL_DIR}/logrotate" "/etc/logrotate.d/ovpnmanager" \
    PANEL_HOME "$PANEL_HOME" PANEL_USER "$PANEL_USER"

  systemctl restart ovpnmanager
  sleep 3
  local pstate; pstate="$(systemctl is-active ovpnmanager 2>&1 || true)"
  [ "$pstate" = "active" ] || { journalctl -u ovpnmanager -n 40 --no-pager 2>/dev/null | tail -40 >&2 || true; die "面板启动失败（${pstate}）"; }
  ok "面板已启动（ovpnmanager.service，监听 127.0.0.1:${PANEL_PORT}）"

  rm -rf "$extract"
}

# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
self_check() {
  step "安装自检"
  local fail=0
  printf '  %-30s %s\n' "OpenVPN 服务" "$(systemctl is-active "$OVPN_SERVICE" 2>&1)"
  if [ "$PANEL_ENABLE" = "1" ]; then
    printf '  %-30s %s\n' "面板服务" "$(systemctl is-active ovpnmanager 2>&1)"
    printf '  %-30s %s\n' "nginx" "$(systemctl is-active nginx 2>&1)"
    if curl -fsS --max-time 10 "http://127.0.0.1:${PANEL_PORT}/api/health/ready" >/dev/null 2>&1; then
      printf '  %-30s %s\n' "面板就绪探针" "OK"
    else
      printf '  %-30s %s\n' "面板就绪探针" "FAILED"; fail=1
    fi
    if curl -fsS -k --max-time 10 -o /dev/null "${PANEL_SCHEME}://127.0.0.1:${PANEL_WEB_PORT}/${WEB_NAME}/"; then
      printf '  %-30s %s\n' "前端页面（经 nginx）" "OK"
    else
      printf '  %-30s %s\n' "前端页面（经 nginx）" "FAILED"; fail=1
    fi
  fi
  printf '  %-30s %s\n' "ip_forward" "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo '?')"
  printf '  %-30s %s\n' "management 端口" "$(ss -tlnp 2>/dev/null | grep -c ":${MGMT_PORT}" || echo 0)"
  [ "$fail" = "0" ] || warn "自检存在失败项，请按上面提示排查"
  return 0
}

print_summary() {
  local ip="$SERVER_ADDR"
  printf '\n'
  printf "${C_OK}==================== 安装完成 ====================${C_RESET}\n"
  printf "  OpenVPN : %s://%s:%s  （服务：%s）\n" "$VPN_PROTO" "$ip" "$VPN_PORT" "$OVPN_SERVICE"
  printf "  客户端配置：%s/%s.ovpn\n" "$CLIENT_EXPORT_DIR" "$CLIENT_NAME"
  if [ "$PANEL_ENABLE" = "1" ]; then
    printf "  面板     : %s://%s:%s/%s/\n" "$PANEL_SCHEME" "$ip" "$PANEL_WEB_PORT" "$WEB_NAME"
    printf "             面板默认仅允许 VPN 网段访问（%s/%s）\n" "$VPN_NETWORK" "$VPN_CIDR"
  fi
  printf "${C_OK}=================================================${C_RESET}\n"
  printf "  常用命令：\n"
  printf "    新增客户端   : sudo bash %s --addclient <名称>\n" "$0"
  printf "    吊销客户端   : sudo bash %s --revokeclient <名称>\n" "$0"
  printf "    列出客户端   : sudo bash %s --listclients\n" "$0"
  printf "    导出客户端   : sudo bash %s --exportclient <名称>\n" "$0"
  printf "    卸载         : sudo bash %s --uninstall\n" "$0"
  printf '\n'
  if [ "$VPN_PROTO" = "udp" ]; then
    printf "  ${C_WARN}提示${C_RESET}：如服务器有外部防火墙/安全组，请放行 UDP %s 端口。\n" "$VPN_PORT"
  else
    printf "  ${C_WARN}提示${C_RESET}：如服务器有外部防火墙/安全组，请放行 TCP %s 端口。\n" "$VPN_PORT"
  fi
  printf '\n'
}

# ---------------------------------------------------------------------------
# 卸载
# ---------------------------------------------------------------------------
uninstall() {
  step "卸载 OpenVPN 与面板"
  require_root
  if [ "$ASSUME_YES" != "1" ]; then
    warn "这会删除 OpenVPN、面板、全部证书与配置，且不可恢复！"
    read -r -p "确认卸载？输入 yes 继续：" ans
    [ "$ans" = "yes" ] || die "已取消"
  fi

  systemctl disable --now ovpnmanager >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/ovpnmanager.service
  systemctl disable --now ovpn-oneclick-nat >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/ovpn-oneclick-nat.service

  local svc
  for svc in $(systemctl list-unit-files 2>/dev/null | grep -oE '^openvpn(-server)?@[a-zA-Z0-9_-]+' | sort -u); do
    systemctl disable --now "$svc" >/dev/null 2>&1 || true
  done

  rm -f /etc/nginx/conf.d/ovpnmanager.conf /etc/nginx/sites-available/ovpnmanager.conf
  rm -f /etc/nginx/sites-enabled/ovpnmanager.conf
  rm -f /etc/sudoers.d/ovpnmanager /etc/logrotate.d/ovpnmanager
  rm -rf /usr/local/lib/ovpnmanager /usr/local/lib/ovpn-oneclick
  # 设置助手的配置与状态（apply.conf / last-apply.conf / apply.log）
  rm -rf "$STATE_DIR"
  rm -rf "$PANEL_HOME" "${WEB_ROOT}/${WEB_NAME}"
  rm -f /etc/sysctl.d/99-ovpn-oneclick.conf

  # 面板用户
  id -u "$PANEL_USER" >/dev/null 2>&1 && userdel -r "$PANEL_USER" >/dev/null 2>&1 || true

  if [ "$ASSUME_YES" = "1" ]; then rm -rf "$OVPN_DIR" "$LOG_DIR"; else
    read -r -p "同时删除 ${OVPN_DIR}（全部证书与配置）？[y/N] " a2
    case "$a2" in [yY]*) rm -rf "$OVPN_DIR" "$LOG_DIR" ;; *) warn "已保留 ${OVPN_DIR}" ;; esac
  fi

  systemctl daemon-reload
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  ok "卸载完成（软件包未删除，如需清理：${PKG} remove openvpn easy-rsa nginx）"
}

# ---------------------------------------------------------------------------
# 特权助手：让面板（非 root）能落地「服务端设置」页面改的配置
#
# 面板不直接改 server.conf / NAT / nginx（那些都归 root），而是把候选配置写到
# 自己的数据目录，再用 sudo 调 apply-config。sudoers 里只允许这一条命令、
# 且不允许参数；助手自己校验候选只能动托管块，失败自动回滚。
# 校验规则被拆到 apply-validate.sh，以便脱离 root 单独测试（见 tests/test_apply_config.sh）。
# ---------------------------------------------------------------------------
install_apply_helper() {
  step "安装设置助手（apply-config）"

  install -D -m 0755 "${TPL_DIR}/apply-config.sh" /usr/local/lib/ovpnmanager/apply-config
  install -D -m 0644 "${TPL_DIR}/apply-validate.sh" /usr/local/lib/ovpnmanager/apply-validate.sh
  chown root:root /usr/local/lib/ovpnmanager/apply-config /usr/local/lib/ovpnmanager/apply-validate.sh
  chmod 0755 /usr/local/lib/ovpnmanager/apply-config
  chmod 0644 /usr/local/lib/ovpnmanager/apply-validate.sh

  # nginx 站点配置文件的位置与 deploy_panel 里后续渲染时用的保持一致
  local nginx_conf="/etc/nginx/conf.d/ovpnmanager.conf"
  [ -d /etc/nginx/sites-available ] && nginx_conf="/etc/nginx/sites-available/ovpnmanager.conf"

  mkdir -p "$STATE_DIR"
  render "${TPL_DIR}/apply.conf.template" "${STATE_DIR}/apply.conf" \
    SERVER_CONF "$SERVER_CONF" \
    OVPN_SERVICE "$OVPN_SERVICE" \
    CANDIDATE "${PANEL_HOME}/backend/data/server.conf.candidate" \
    PANEL_USER "$PANEL_USER" \
    LOG_DIR "$LOG_DIR" \
    NAT_SCRIPT "/usr/local/lib/ovpn-oneclick/nat.sh" \
    NGINX_CONF "$nginx_conf" \
    STATE_DIR "$STATE_DIR" \
    WAIT_ACTIVE_SECONDS "20"
  chown root:root "${STATE_DIR}/apply.conf"
  chmod 0644 "${STATE_DIR}/apply.conf"

  # 助手要能写 server.conf 所在目录（备份/原子替换）与 nginx、NAT 脚本——它以 root 运行，天然可以。
  # 这里只做一次自检：助手必须存在且可被 bash 解析。
  bash -n /usr/local/lib/ovpnmanager/apply-config \
    || die "apply-config 语法校验失败（${TPL_DIR}/apply-config.sh 是否有误？）"
  bash -n /usr/local/lib/ovpnmanager/apply-validate.sh \
    || die "apply-validate.sh 语法校验失败"

  # 面板需要能写候选文件：数据目录已归面板用户，这里确保一下
  install -d -o "$PANEL_USER" -g "$PANEL_USER" -m 0750 "${PANEL_HOME}/backend/data" 2>/dev/null || true

  # 特权助手通道：socket 激活（免疫 NoNewPrivileges）
# --------------------------------------------------
# 云厂商的机器常给面板进程施加 NoNewPrivileges，导致面板内**任何 sudo 都失败**：
#   sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
# 从 unit 里关不掉（实测 unit 无该设置、systemd 也报 no，进程里仍是 1）。
# 而 systemd 以 root 启动 User=root 的服务不需要"获取"权限，因此改走 socket 激活：
# 面板连 /run/ovpn-oneclick/apply.sock 发一行动作，systemd 拉起 root 助手来处理。
install -D -m 0755 "${TPL_DIR}/apply-dispatch.sh" /usr/local/lib/ovpnmanager/apply-dispatch
render "${TPL_DIR}/apply.socket" /etc/systemd/system/apply.socket PANEL_USER "$PANEL_USER"
install -D -m 0644 "${TPL_DIR}/apply@.service" /etc/systemd/system/apply@.service
systemctl daemon-reload >/dev/null 2>&1 || true
if systemctl enable --now apply.socket >/dev/null 2>&1; then
  ok "特权助手通道就绪：apply.socket（socket 激活，不依赖 sudo）"
else
  warn "apply.socket 启用失败，面板将退回 sudo 通道（受 NoNewPrivileges 限制的环境会失败）"
fi

ok "设置助手就绪：/usr/local/lib/ovpnmanager/apply-config（sudoers 已授权且不接受参数）"
}

# ---------------------------------------------------------------------------
# 特权助手（第二个）：让面板能落地「面板设置」页改的 nginx 站点配置
#
# 与 apply-config **并列**，不合并：apply-config 管 server.conf（OpenVPN 服务端），
# 本助手管 nginx 站点配置（面板入口：端口 / 域名 / 放行来源 / TLS）。
# 分开的好处是 sudoers 里两条授权各自只够改自己那一块。
#
# 两者复用同一份东西：
#   * templates/apply-validate.sh 的 candidate_file_problem()（候选文件校验）；
#   * templates/panel-nginx.sh    的 panel_nginx_render()（nginx 渲染，安装脚本也用）。
# 校验规则在 templates/panel-validate.sh（可脱离 root 单测，见 tests/test_panel_config.sh）。
# ---------------------------------------------------------------------------
install_panel_helper() {
  step "安装面板设置助手（apply-panel-config）"

  install -D -m 0644 "${TPL_DIR}/panel-validate.sh" /usr/local/lib/ovpnmanager/panel-validate.sh
  install -D -m 0644 "${TPL_DIR}/panel-nginx.sh" /usr/local/lib/ovpnmanager/panel-nginx.sh
  install -D -m 0755 "${TPL_DIR}/apply-panel-config.sh" /usr/local/lib/ovpnmanager/apply-panel-config
  # 站点模板：助手重新渲染时以它为唯一来源（面板里保存设置就靠它）
  install -D -m 0644 "${TPL_DIR}/nginx.conf" /usr/local/lib/ovpnmanager/nginx.conf.template
  chown root:root \
    /usr/local/lib/ovpnmanager/panel-validate.sh \
    /usr/local/lib/ovpnmanager/panel-nginx.sh \
    /usr/local/lib/ovpnmanager/apply-panel-config \
    /usr/local/lib/ovpnmanager/nginx.conf.template
  chmod 0644 /usr/local/lib/ovpnmanager/panel-validate.sh /usr/local/lib/ovpnmanager/panel-nginx.sh \
    /usr/local/lib/ovpnmanager/nginx.conf.template
  chmod 0755 /usr/local/lib/ovpnmanager/apply-panel-config

  # nginx 站点配置文件的位置与 deploy_panel 里渲染时用的保持一致
  local nginx_conf="/etc/nginx/conf.d/ovpnmanager.conf"
  [ -d /etc/nginx/sites-available ] && nginx_conf="/etc/nginx/sites-available/ovpnmanager.conf"

  # 自签证书目录（0700）与 nginx 配置备份目录：助手以 root 写入，这里先建好
  mkdir -p "$STATE_DIR" "$PANEL_TLS_DIR" "$PANEL_BACKUP_DIR"
  chown root:root "$STATE_DIR" "$PANEL_TLS_DIR" "$PANEL_BACKUP_DIR" 2>/dev/null || true
  chmod 0755 "$STATE_DIR" 2>/dev/null || true
  chmod 0700 "$PANEL_TLS_DIR" 2>/dev/null || true
  chmod 0750 "$PANEL_BACKUP_DIR" 2>/dev/null || true

  render "${TPL_DIR}/panel.conf.template" "${STATE_DIR}/panel.conf" \
    NGINX_CONF "$nginx_conf" \
    STATE_DIR "$STATE_DIR" \
    CANDIDATE "${PANEL_HOME}/backend/data/panel.candidate" \
    PANEL_USER "$PANEL_USER" \
    WEB_ROOT "$WEB_ROOT" \
    WEB_NAME "$WEB_NAME" \
    BACKUP_DIR "$PANEL_BACKUP_DIR" \
    TLS_DIR "$PANEL_TLS_DIR" \
    WAIT_RELOAD_SECONDS "10" \
    PANEL_PORT "$PANEL_PORT" \
    NGINX_SERVICE "nginx" \
    PANEL_SERVICE "ovpnmanager" \
    SERVER_CONF "$SERVER_CONF" \
    NGINX_TEMPLATE "/usr/local/lib/ovpnmanager/nginx.conf.template" \
    BACKUP_KEEP "5"
  chown root:root "${STATE_DIR}/panel.conf"
  chmod 0644 "${STATE_DIR}/panel.conf"

  bash -n /usr/local/lib/ovpnmanager/apply-panel-config \
    || die "apply-panel-config 语法校验失败（${TPL_DIR}/apply-panel-config.sh 是否有误？）"
  bash -n /usr/local/lib/ovpnmanager/panel-validate.sh \
    || die "panel-validate.sh 语法校验失败"
  bash -n /usr/local/lib/ovpnmanager/panel-nginx.sh \
    || die "panel-nginx.sh 语法校验失败"

  ok "面板设置助手就绪：/usr/local/lib/ovpnmanager/apply-panel-config（sudoers 已授权且不接受参数）"
}

# ---------------------------------------------------------------------------
# 面板自身的访问设置：读 panel.state（面板里改过的值），并据此渲染 nginx
#
# **这是"升级不冲掉用户改动"的关键**：重装 / --update 都会重新渲染 nginx，
# 若只用环境变量/默认值，用户在面板里改过的端口就会静默退回 5698。
# 所以渲染前先读 panel.state，把值覆盖到 PANEL_WEB_PORT / PANEL_SERVER_NAME /
# PANEL_ALLOW_* / PANEL_TLS_* 上；读取时逐项校验，任何一项不合法就退回默认值
# （宁可回到默认也不能把 nginx 写成起不来的样子）。
# ---------------------------------------------------------------------------
load_panel_state() {
  PANEL_STATE_LOADED=0
  if [ ! -f "$PANEL_STATE_FILE" ]; then
    info "未找到 ${PANEL_STATE_FILE}，面板访问设置使用环境变量/默认值"
    derive_panel_allow
    PANEL_SCHEME="http"
    [ "$PANEL_TLS_MODE" = "off" ] || PANEL_SCHEME="https"
    return 0
  fi

  local value applied=0
  value="$(panel_state_value "$PANEL_STATE_FILE" PANEL_LISTEN_PORT)"
  if [ -n "$value" ] && [ -z "$(panel_port_problem "$value" "$VPN_PORT")" ]; then
    PANEL_WEB_PORT="$value"; applied=1
  elif [ -n "$value" ]; then
    warn "panel.state 里的监听端口 ${value} 不合法，已忽略"
  fi

  value="$(panel_state_value "$PANEL_STATE_FILE" PANEL_SERVER_NAME)"
  if [ -n "$value" ] && [ -z "$(panel_server_name_problem "$value")" ]; then
    PANEL_SERVER_NAME="$value"
  elif [ -n "$value" ]; then
    warn "panel.state 里的绑定域名 ${value} 不合法，已忽略"
  fi

  value="$(panel_state_value "$PANEL_STATE_FILE" PANEL_ALLOW_MODE)"
  if [ -n "$value" ] && [ -z "$(panel_allow_problem "$value" "")" ]; then
    PANEL_ALLOW_MODE="$value"
  elif [ -n "$value" ]; then
    warn "panel.state 里的放行来源模式 ${value} 不合法，已忽略"
  fi

  value="$(panel_state_value "$PANEL_STATE_FILE" PANEL_ALLOW_CIDRS)"
  if [ "$PANEL_ALLOW_MODE" = "custom" ]; then
    if [ -z "$(panel_allow_problem custom "$value")" ]; then
      PANEL_ALLOW_CIDRS="$value"
    else
      warn "panel.state 里的自定义放行来源不合法，已退回默认（仅 VPN 网段）"
      PANEL_ALLOW_MODE="vpn"; PANEL_ALLOW_CIDRS=""
    fi
  else
    PANEL_ALLOW_CIDRS=""
  fi

  value="$(panel_state_value "$PANEL_STATE_FILE" PANEL_TLS_MODE)"
  if [ -n "$value" ] && [ -z "$(panel_tls_problem "$value" "" "")" ]; then
    PANEL_TLS_MODE="$value"
  elif [ -n "$value" ]; then
    warn "panel.state 里的 TLS 模式 ${value} 不合法，已按 off 处理"
    PANEL_TLS_MODE="off"
  fi

  if [ "$PANEL_TLS_MODE" = "existing" ]; then
    PANEL_TLS_CERT="$(panel_state_value "$PANEL_STATE_FILE" PANEL_TLS_CERT)"
    PANEL_TLS_KEY="$(panel_state_value "$PANEL_STATE_FILE" PANEL_TLS_KEY)"
    if [ -n "$(panel_tls_problem existing "$PANEL_TLS_CERT" "$PANEL_TLS_KEY")" ]; then
      # 证书被删/被替换/权限变了都不该让整个安装失败：退回 off 并提示用户重配
      warn "panel.state 里的 TLS 证书不可用（$(panel_tls_problem existing "$PANEL_TLS_CERT" "$PANEL_TLS_KEY")），本次按 off 渲染"
      PANEL_TLS_MODE="off"; PANEL_TLS_CERT=""; PANEL_TLS_KEY=""
    fi
  elif [ "$PANEL_TLS_MODE" = "selfsigned" ]; then
    # 自签证书由助手生成在 TLS_DIR；这里只检查它还在不在
    if [ -s "${PANEL_TLS_DIR}/panel-selfsigned.crt" ] && [ -s "${PANEL_TLS_DIR}/panel-selfsigned.key" ]; then
      PANEL_TLS_CERT="${PANEL_TLS_DIR}/panel-selfsigned.crt"
      PANEL_TLS_KEY="${PANEL_TLS_DIR}/panel-selfsigned.key"
    else
      warn "panel.state 说用自签证书，但 ${PANEL_TLS_DIR} 里没有证书，本次按 off 渲染（可在面板里重新开启）"
      PANEL_TLS_MODE="off"; PANEL_TLS_CERT=""; PANEL_TLS_KEY=""
    fi
  else
    PANEL_TLS_CERT=""; PANEL_TLS_KEY=""
  fi

  [ -z "$PANEL_ALLOW_MODE" ] && derive_panel_allow

  PANEL_SCHEME="http"
  [ "$PANEL_TLS_MODE" = "off" ] || PANEL_SCHEME="https"

  if [ "$applied" = "1" ]; then
    info "已从 ${PANEL_STATE_FILE} 恢复面板访问设置：端口 ${PANEL_WEB_PORT}，域名 ${PANEL_SERVER_NAME}，放行 ${PANEL_ALLOW_MODE}，TLS ${PANEL_TLS_MODE}"
  fi
  PANEL_STATE_LOADED=1
  return 0
}

# 没有 panel.state 时的放行来源：保持历史行为
#   * 给了 PANEL_ALLOW：custom = VPN 网段 + 用户给的来源（旧版就是把它们追加进去的）
#   * 没给：vpn（只放行 VPN 网段与本机）
derive_panel_allow() {
  if [ -n "${PANEL_ALLOW_MODE:-}" ]; then
    return 0
  fi
  if [ -n "${PANEL_ALLOW:-}" ]; then
    local extra; extra="$(printf '%s' "$PANEL_ALLOW" | tr -d '[:space:]')"
    if [ -n "$extra" ]; then
      PANEL_ALLOW_MODE="custom"
      PANEL_ALLOW_CIDRS="${VPN_NETWORK}/${VPN_CIDR},${extra}"
      info "按 PANEL_ALLOW 推导放行来源：custom（${PANEL_ALLOW_CIDRS}）"
      return 0
    fi
  fi
  PANEL_ALLOW_MODE="vpn"
  PANEL_ALLOW_CIDRS=""
  return 0
}

# 用与 root 助手**完全相同**的渲染实现生成站点配置（templates/panel-nginx.sh）
render_panel_nginx() { # render_panel_nginx <目标文件>
  local dest="$1"
  [ -n "$PANEL_ALLOW_MODE" ] || derive_panel_allow
  if ! PANEL_LISTEN_PORT="$PANEL_WEB_PORT" \
       PANEL_SERVER_NAME="${PANEL_SERVER_NAME:-_}" \
       PANEL_ALLOW_MODE="$PANEL_ALLOW_MODE" \
       PANEL_ALLOW_CIDRS="$PANEL_ALLOW_CIDRS" \
       PANEL_TLS_MODE="$PANEL_TLS_MODE" \
       PANEL_TLS_CERT="$PANEL_TLS_CERT" \
       PANEL_TLS_KEY="$PANEL_TLS_KEY" \
       PANEL_PORT="$PANEL_PORT" \
       VPN_CIDR="${VPN_NETWORK}/${VPN_CIDR}" \
       WEB_ROOT="$WEB_ROOT" \
       WEB_NAME="$WEB_NAME" \
       panel_nginx_render "${TPL_DIR}/nginx.conf" "$dest"; then
    die "渲染 ${dest} 失败（面板访问设置不合法？）"
  fi
  return 0
}

# 把本次实际渲染用的值写进 panel.state：下次重装/升级就从这个文件恢复
write_panel_state() {
  mkdir -p "$STATE_DIR"
  panel_write_state "$PANEL_STATE_FILE" \
    PANEL_LISTEN_PORT "$PANEL_WEB_PORT" \
    PANEL_SERVER_NAME "${PANEL_SERVER_NAME:-_}" \
    PANEL_ALLOW_MODE "${PANEL_ALLOW_MODE:-vpn}" \
    PANEL_ALLOW_CIDRS "${PANEL_ALLOW_CIDRS:-}" \
    PANEL_TLS_MODE "${PANEL_TLS_MODE:-off}" \
    PANEL_TLS_CERT "${PANEL_TLS_CERT:-}" \
    PANEL_TLS_KEY "${PANEL_TLS_KEY:-}"
  chown root:root "$PANEL_STATE_FILE" 2>/dev/null || true
  chmod 0644 "$PANEL_STATE_FILE" 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
# 已存在安装的检测与采纳
#
# 这一节解决的是"两个项目缝合"最容易出事的地方：机器上已经有一套别人脚本
# （hwdsl2 / angristan / 博客里的 centos7-install-openvpn-v2.sh）装好的 OpenVPN，
# 其目录布局与本脚本不同：
#
#     /etc/openvpn/server/{server.conf, easy-rsa/, crl.pem, tc.key}
#
# 直接跑本脚本会踩两个坑：
#   1. tls-crypt 密钥叫 tc.key，而本脚本默认叫 ta.key —— 若重新生成一份，
#      服务端换了密钥，**所有既有客户端的 .ovpn 立刻全部失效**；
#   2. server.conf 会被整份重写，丢掉对方脚本写入的定制项。
# 所以这里先探测，再要求显式 --adopt / --force。
# ---------------------------------------------------------------------------
EXISTING_FOUND=0
EXISTING_PKI_DIR=""

detect_existing_install() {
  local conf=""
  local c
  for c in "${SERVER_DIR}/${SERVER_CN}.conf" "${OVPN_DIR}/${SERVER_CN}.conf" "${OVPN_DIR}/server.conf"; do
    if [ -f "$c" ]; then conf="$c"; break; fi
  done
  if [ -z "$conf" ] && [ -d "$OVPN_DIR" ]; then
    conf="$(find "$OVPN_DIR" -maxdepth 2 -type f -name '*.conf' 2>/dev/null | sort | sed -n '1p' || true)"
  fi
  EXISTING_SERVER_CONF="$conf"

  # tls-crypt / tls-auth 密钥：各家命名不同，逐个试
  local k
  for k in \
    "${SERVER_DIR}/tc.key" "${OVPN_DIR}/tc.key" \
    "${SERVER_DIR}/tls-crypt.key" "${OVPN_DIR}/tls-crypt.key" \
    "${SERVER_DIR}/ta.key" "${OVPN_DIR}/ta.key" \
    "${SERVER_DIR}/tls-auth.key" "${OVPN_DIR}/tls-auth.key"; do
    if [ -s "$k" ]; then EXISTING_TLS_KEY="$k"; break; fi
  done
  if [ -n "$EXISTING_TLS_KEY" ]; then
    case "$EXISTING_TLS_KEY" in
      *tc.key|*tls-crypt*) EXISTING_TLS_MODE="tls-crypt" ;;
      *)                   EXISTING_TLS_MODE="tls-auth" ;;
    esac
  fi

  # 既有 PKI
  local d
  for d in "${SERVER_DIR}/easy-rsa" "${OVPN_DIR}/easy-rsa" "${OVPN_DIR}/easyrsa"; do
    if [ -d "${d}/pki" ]; then EXISTING_PKI_DIR="$d"; break; fi
  done

  if [ -n "$EXISTING_SERVER_CONF" ] || [ -n "$EXISTING_PKI_DIR" ]; then
    EXISTING_FOUND=1
  fi
  return 0
}

report_existing_install() {
  warn "检测到机器上已经有一套 OpenVPN 安装："
  [ -n "$EXISTING_SERVER_CONF" ] && printf '    server.conf : %s\n' "$EXISTING_SERVER_CONF"
  [ -n "$EXISTING_PKI_DIR" ]     && printf '    PKI         : %s/pki\n' "$EXISTING_PKI_DIR"
  [ -n "$EXISTING_TLS_KEY" ]     && printf '    tls 密钥    : %s（%s）\n' "$EXISTING_TLS_KEY" "$EXISTING_TLS_MODE"
  # 必须显式 return 0：这是纯信息输出函数，而它的最后一条语句是
  # `[ -n ... ] && printf`。当该测试为假（没有 tls 密钥）时整条语句返回 1，
  # 函数便返回 1；在 set -e 下调用点会被当成"命令失败"而中断整个安装。
  # 结果就是"在有既有 OpenVPN 的机器上重跑"这条路（含 --adopt / --force）全部崩掉。
  return 0
}

# 采纳既有布局：把本脚本的路径对齐到对方的实际位置，避免造出第二套 PKI
adopt_layout() {
  if [ -n "$EXISTING_SERVER_CONF" ]; then
    SERVER_CONF="$EXISTING_SERVER_CONF"
    # 服务实例名要与配置路径匹配，否则 systemd 加载不到这份配置
    local base; base="$(basename "$SERVER_CONF" .conf)"
    if [ "$base" != "$SERVER_CN" ]; then
      info "既有配置名为 ${base}.conf，服务实例改用 ${base}"
      SERVER_CN="$base"
    fi
    # 服务名必须由**配置路径**决定（而不是"哪个 unit 存在"）。
    # /etc/openvpn/server/<cn>.conf -> openvpn-server@<cn>
    # /etc/openvpn/<cn>.conf       -> openvpn@<cn>
    # 只看 unit 会把既有配置配给读另一个路径的服务，导致启动失败。
    case "$SERVER_CONF" in
      "${SERVER_DIR}"/*) OVPN_SERVICE="openvpn-server@${SERVER_CN}" ;;
      *)                 OVPN_SERVICE="openvpn@${SERVER_CN}" ;;
    esac
  fi
  if [ -n "$EXISTING_PKI_DIR" ]; then
    EASYRSA_DIR="$EXISTING_PKI_DIR"
    info "复用既有 PKI：${EASYRSA_DIR}/pki"
  fi
  if [ -n "$EXISTING_TLS_KEY" ]; then
    TA_KEY="$EXISTING_TLS_KEY"
  fi
  local confdir; confdir="$(dirname "$SERVER_CONF")"
  local c
  for c in "${confdir}/crl.pem" "${OVPN_DIR}/crl.pem" "${EASYRSA_DIR}/pki/crl.pem"; do
    if [ -f "$c" ]; then CRL_PATH="$c"; break; fi
  done
  # CCD 与客户端导出目录：优先沿用配置里已声明的 client-config-dir
  local declared_ccd=""
  if [ -f "$SERVER_CONF" ]; then
    declared_ccd="$(grep -oP '^\s*client-config-dir\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
  fi
  [ -n "$declared_ccd" ] && CCD_DIR="$declared_ccd"
  if [ "$confdir" = "${SERVER_DIR}" ]; then
    CLIENT_EXPORT_DIR="${SERVER_DIR}/client"
  else
    CLIENT_EXPORT_DIR="${OVPN_DIR}/client"
  fi

  # 从对方配置里读出真实的网段/掩码/协议/端口。
  # 不读的话，后面的 NAT 规则、nginx 允许网段、面板的网段假设都会与真实隧道不一致
  # （例如对方用 10.8.0.0/24，而本脚本的默认是 10.8.44.0/24）。
  if [ -f "$SERVER_CONF" ]; then
    local v
    v="$(grep -oP '^\s*server\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
    if [ -n "$v" ]; then
      VPN_NETWORK="${v%.*}.0"
    fi
    v="$(grep -oP '^\s*server\s+\S+\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
    if [ -n "$v" ] && VPN_CIDR="$(mask_to_cidr "$v" 2>/dev/null)" && [ -n "$VPN_CIDR" ]; then
      VPN_NETMASK="$v"
    else
      VPN_CIDR=24
      VPN_NETMASK="$(cidr_to_mask "$VPN_CIDR")"
    fi
    v="$(grep -oP '^\s*proto\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
    case "$v" in udp|tcp) VPN_PROTO="$v" ;; esac
    v="$(grep -oP '^\s*port\s+\K\S+' "$SERVER_CONF" 2>/dev/null | head -1 || true)"
    case "$v" in ''|*[!0-9]*) ;; *) VPN_PORT="$v" ;; esac
    info "从既有配置读取：网段 ${VPN_NETWORK}/${VPN_CIDR} proto=${VPN_PROTO} port=${VPN_PORT}"
  fi
  info "采纳后的关键路径：service=${OVPN_SERVICE} conf=${SERVER_CONF} tls=${TA_KEY} crl=${CRL_PATH} ccd=${CCD_DIR}"
}

# 往既有 server.conf 里补上面板需要的指令，而不是整份替换。
# 返回 0 表示补过，1 表示无需改动。
inject_panel_directives() {
  [ -f "$SERVER_CONF" ] || return 1        # 真正的错误：目标配置不存在
  local changed=0
  local pattern

  # 先判断到底缺不缺：不缺就直接返回成功，**不要留下无意义的备份**
  # （早期版本无条件 cp -a 备份，于是每次 --adopt 都会多一个 .bak 文件）
  local missing=0
  for pattern in '^\s*management\s' '^\s*status\s' '^\s*status-version\s' \
                 '^\s*client-config-dir\s' '^\s*crl-verify\s'; do
    grep -qE "$pattern" "$SERVER_CONF" 2>/dev/null || missing=1
  done
  if [ "$missing" = "0" ]; then
    info "既有 server.conf 已包含面板需要的全部指令，未改动"
    # 注意这里必须返回 0："配置已经齐了"在安装视角下是**成功**。
    # 早期版本返回 1，调用方又用 `|| true` 把失败吞掉；一旦去掉 || true
    # （为了不掩盖真正的错误），这个返回值就会让 --adopt 整个中断。
    return 0
  fi

  add_if_missing() { # add_if_missing <匹配正则> <要追加的行>
    if ! grep -qE "$1" "$SERVER_CONF" 2>/dev/null; then
      printf '%s\n' "$2" >> "$SERVER_CONF"
      changed=1
      info "已补充：$2"
    fi
  }
  cp -a "$SERVER_CONF" "${SERVER_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
  add_if_missing '^\s*management\s'        "management 127.0.0.1 ${MGMT_PORT}"
  add_if_missing '^\s*status\s'            "status ${LOG_DIR}/openvpn-status.log"
  add_if_missing '^\s*status-version\s'    "status-version 3"
  add_if_missing '^\s*client-config-dir\s' "client-config-dir ${CCD_DIR}"
  if ! grep -qE '^\s*crl-verify\s' "$SERVER_CONF" 2>/dev/null; then
    [ -f "$CRL_PATH" ] || refresh_crl
    add_if_missing '^\s*crl-verify\s' "crl-verify ${CRL_PATH}"
  fi
  [ "$changed" = "1" ] && ok "已在既有 server.conf 中补上面板需要的指令（原文件已备份）"
  return 0
}

# ---------------------------------------------------------------------------
# 安装状态与升级
# ---------------------------------------------------------------------------
save_state() {
  mkdir -p "$STATE_DIR"
  {
    echo "# 由 ovpn-oneclick 写入，供 --check-update / --update 使用。请勿手工编辑。"
    echo "INSTALLED_VERSION=\"${UPDATED_VERSION:-${SCRIPT_VERSION}}\""
    echo "INSTALLED_AT=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
    echo "OVPN_SERVICE=\"${OVPN_SERVICE}\""
    echo "SERVER_CN=\"${SERVER_CN}\""
    echo "SERVER_CONF=\"${SERVER_CONF}\""
    echo "EASYRSA_DIR=\"${EASYRSA_DIR}\""
    echo "CCD_DIR=\"${CCD_DIR}\""
    echo "CLIENT_EXPORT_DIR=\"${CLIENT_EXPORT_DIR}\""
    echo "CRL_PATH=\"${CRL_PATH}\""
    echo "TA_KEY=\"${TA_KEY}\""
    echo "MGMT_PORT=\"${MGMT_PORT}\""
    echo "VPN_PORT=\"${VPN_PORT}\""
    echo "VPN_PROTO=\"${VPN_PROTO}\""
    echo "FULL_TUNNEL=\"${FULL_TUNNEL}\""
    echo "PANEL_ENABLE=\"${PANEL_ENABLE}\""
    echo "PANEL_PORT=\"${PANEL_PORT}\""
  echo "PANEL_WEB_PORT=\"${PANEL_WEB_PORT}\""
  echo "PANEL_ALLOW=\"${PANEL_ALLOW}\""
    echo "PANEL_HOME=\"${PANEL_HOME}\""
    echo "WEB_NAME=\"${WEB_NAME}\""
    echo "WEB_ROOT=\"${WEB_ROOT}\""
    echo "PUBLIC_IP=\"${SERVER_ADDR}\""
    echo "REPO_OWNER=\"${REPO_OWNER}\""
    echo "REPO_NAME=\"${REPO_NAME}\""
    echo "REPO_BRANCH=\"${REPO_BRANCH}\""
  } > "$STATE_FILE"
  chmod 0644 "$STATE_FILE"
}

load_state() {
  [ -f "$STATE_FILE" ] || return 1
  # shellcheck disable=SC1090
  . "$STATE_FILE"
  return 0
}

installed_version() {
  if load_state 2>/dev/null; then printf '%s' "${INSTALLED_VERSION:-unknown}"; else printf 'unknown'; fi
}

remote_version() {
  local v=""
  v="$(curl -fsSL --max-time 20 "$REMOTE_VERSION_URL" 2>/dev/null | tr -d '[:space:]')" || v=""
  if [ -z "$v" ]; then
    # 回退一：无扩展名的 VERSION（老仓库布局 / 某些平台对无后缀路径返回 404）
    v="$(curl -fsSL --max-time 20 "${ASSET_BASE}/VERSION" 2>/dev/null | tr -d '[:space:]')" || v=""
  fi
  if [ -z "$v" ]; then
    # 回退二：直接从远端 install.sh 里读 SCRIPT_VERSION。
    # 只要脚本本身能下载到，版本检查就永远可用——这是"推送新版本还能升级"的最后保障。
    v="$(curl -fsSL --max-time 40 "$SELF_URL" 2>/dev/null | grep -m1 '^SCRIPT_VERSION=' | cut -d'"' -f2)" || v=""
  fi
  printf '%s' "$v"
}

version_gt() { # A > B ?
  [ "$1" = "$2" ] && return 1
  local top
  top="$(printf '%s\n%s\n' "$1" "$2" | sort -V 2>/dev/null | tail -1)" || top=""
  [ -n "$top" ] || top="$1"
  [ "$top" = "$1" ]
}

check_update() {
  step "检查更新"
  local cur remote
  cur="$(installed_version)"
  remote="$(remote_version)"
  printf '  已安装版本 : %s\n' "$cur"
  printf '  当前脚本   : %s\n' "$SCRIPT_VERSION"
  if [ -z "$remote" ]; then
    warn "取不到远端版本（${REMOTE_VERSION_URL}），请检查网络或 REPO_OWNER/REPO_NAME"
    return 1
  fi
  printf '  远端最新版 : %s\n' "$remote"
  if [ "$cur" = "unknown" ]; then
    warn "没有找到 ${STATE_FILE}（可能是 1.1.0 之前的版本安装的）"
    info "直接执行 sudo bash install.sh --update 即可覆盖升级"
    return 0
  fi
  if version_gt "$remote" "$cur"; then
    ok "有新版本：${cur} -> ${remote}"
    info "执行 sudo bash install.sh --update 升级"
  else
    ok "已是最新版本"
  fi
  return 0
}

backup_current() {
  mkdir -p "$PANEL_BACKUP_DIR"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local tag="${PANEL_BACKUP_DIR}/panel-${stamp}"
  if [ -d "${PANEL_HOME}/backend" ]; then
    # 只备份配置与数据库（venv 巨大且可重建，不备份）
    tar -czf "${tag}.tar.gz" -C "$PANEL_HOME" \
      --exclude='.venv' --exclude='__pycache__' backend/.env backend/data 2>/dev/null || true
    ok "已备份配置与数据库：${tag}.tar.gz"
  fi
  [ -f "$STATE_FILE" ] && cp -a "$STATE_FILE" "${tag}.state.conf" 2>/dev/null || true
  ls -1t "${PANEL_BACKUP_DIR}"/panel-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f || true
  return 0
}

# 只把模板里"新版本新增、而现有 .env 没有"的键追加进去，
# 既有键一律不动 —— 这是"推送新版本不会把老装机搞坏"的关键。
merge_env() { # merge_env <已渲染的新模板> <现有 .env>
  local tpl="$1" dst="$2" appended=0
  [ -f "$tpl" ] && [ -f "$dst" ] || return 0
  local line key
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    key="${key%"${key##*[![:space:]]}"}"   # 去尾部空白
    case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    if grep -qiE "^[[:space:]]*${key}[[:space:]]*=" "$dst"; then
      continue
    fi
    printf '\n# 由 --update 补充（新版本新增项，沿用模板默认值）\n%s\n' "$line" >> "$dst"
    appended=$((appended + 1))
  done < "$tpl"
  if [ "$appended" -gt 0 ]; then
    ok ".env 已补充 ${appended} 个新增键（未修改任何既有值）"
  fi
  return 0
}

# 自更新安装脚本：下载 → 校验 → 原子替换（rename，避免正在执行的脚本被截断）
self_update() {
  local tmp; tmp="$(mktemp)"
  info "检查安装脚本自身是否有更新…"
  if ! curl -fsSL --max-time 60 -o "$tmp" "$SELF_URL" 2>/dev/null; then
    rm -f "$tmp"; info "无法下载最新脚本，跳过自更新（不影响面板升级）"; return 0
  fi
  if ! head -1 "$tmp" 2>/dev/null | grep -q 'bash'; then
    rm -f "$tmp"; warn "下载到的内容不像 bash 脚本，放弃自更新"; return 0
  fi
  if ! bash -n "$tmp" 2>/dev/null; then
    rm -f "$tmp"; warn "新脚本语法校验未通过，放弃自更新（避免把脚本写坏）"; return 0
  fi
  local newver; newver="$(grep -m1 '^SCRIPT_VERSION=' "$tmp" 2>/dev/null | cut -d'"' -f2 || true)"
  if [ -n "$newver" ] && [ "$newver" = "$SCRIPT_VERSION" ]; then
    rm -f "$tmp"; info "安装脚本已是最新（${SCRIPT_VERSION}）"; return 0
  fi
  local self="${BASH_SOURCE[0]}"
  if [ -f "$self" ] && [ -w "$self" ]; then
    cp -a "$self" "${self}.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    install -m 0755 "$tmp" "${self}.new" && mv -f "${self}.new" "$self"
    UPDATED_VERSION="$newver"
    ok "安装脚本已更新：${SCRIPT_VERSION} -> ${newver:-新版本}"
  else
    info "当前不是从磁盘文件执行的脚本（例如 curl | bash），跳过自更新"
  fi
  rm -f "$tmp"
  return 0
}

do_update() {
  step "升级 ovpn-oneclick"
  require_root
  local before; before="$(installed_version)"

  if load_state 2>/dev/null; then
    info "从状态文件恢复上次安装的路径配置（${STATE_FILE}）"
  else
    warn "未找到 ${STATE_FILE}，按默认路径升级"
  fi

  self_update
  backup_current

  detect_os
  resolve_openvpn_layout
  # 升级绝不重新生成 PKI / server.conf / SECRET_KEY / 管理员口令：
  # PKI 与 tls 密钥一律沿用既有文件，deploy_panel 会保留 .env 与数据库。
  EXISTING_FOUND=0; EXISTING_TLS_KEY=""; EXISTING_TLS_MODE=""
  EXISTING_PKI_DIR=""; EXISTING_SERVER_CONF=""
  detect_existing_install
  if [ "$EXISTING_FOUND" = "1" ]; then
    # 对齐到既有布局（复用 PKI / tls 密钥 / server.conf / 网段 / 协议 / 端口）
    adopt_layout
  fi

  deploy_panel
  self_check
  save_state
  ok "升级完成：${before} -> ${SCRIPT_VERSION}"
  print_summary
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
  require_root
  printf "${C_INFO}ovpn-oneclick v%s —— OpenVPN 服务端 + 管理面板${C_RESET}\n" "$SCRIPT_VERSION"

  case "$ACTION" in
    listclients)
      detect_os >/dev/null 2>&1 || true
      resolve_openvpn_layout
      step "已有客户端"
      list_clients
      exit 0
      ;;
    addclient)     detect_os >/dev/null 2>&1 || true; resolve_openvpn_layout; add_client "$CLIENT_NAME"; print_summary; exit 0 ;;
    revokeclient)  detect_os >/dev/null 2>&1 || true; resolve_openvpn_layout; revoke_client "$CLIENT_NAME"; exit 0 ;;
    exportclient)  detect_os >/dev/null 2>&1 || true; resolve_openvpn_layout; export_client "$CLIENT_NAME"; exit 0 ;;
    uninstall)     detect_os; uninstall; exit 0 ;;
    check-update)  check_update; exit $? ;;
    update)        do_update; exit 0 ;;
  esac

  detect_os
  install_packages
  resolve_openvpn_layout

  # ---- 已存在安装的处理（缝合两个项目时最容易出事的地方）----
  # 无论是否 --adopt，都先探测：只要发现既有的 tls 密钥/PKI，就一律复用。
  # 重新生成 tls-crypt 密钥会让**所有既有客户端配置立即失效**，绝不能默认这么干。
  detect_existing_install
  local ours=0
  if [ -f "$STATE_FILE" ]; then
    local prev_conf
    prev_conf="$(grep -oP '^SERVER_CONF="\K[^"]*' "$STATE_FILE" 2>/dev/null | head -1 || true)"
    if [ -n "$prev_conf" ] && [ "$prev_conf" = "$EXISTING_SERVER_CONF" ]; then
      ours=1
    fi
  fi

  ADOPTED=0
  if [ "$EXISTING_FOUND" = "1" ]; then
    if [ "$ours" = "1" ]; then
      info "检测到本脚本此前安装过（${EXISTING_SERVER_CONF}），按重装/修复处理"
      [ -n "$EXISTING_TLS_KEY" ] && { TA_KEY="$EXISTING_TLS_KEY"; info "复用既有 tls 密钥：${TA_KEY}"; }
      [ -n "$EXISTING_PKI_DIR" ] && EASYRSA_DIR="$EXISTING_PKI_DIR"
    elif [ "$ADOPT_EXISTING" = "1" ]; then
      report_existing_install
      info "--adopt：复用既有 PKI 与 tls 密钥，只往 server.conf 补面板需要的指令"
      adopt_layout
      ADOPTED=1
    elif [ "$FORCE_REWRITE" = "1" ]; then
      report_existing_install
      warn "--force：将备份后重写 ${EXISTING_SERVER_CONF}（PKI 与 tls 密钥仍复用，不会让客户端失效）"
      [ -n "$EXISTING_TLS_KEY" ] && { TA_KEY="$EXISTING_TLS_KEY"; warn "复用既有 tls 密钥：${TA_KEY}"; }
      [ -n "$EXISTING_PKI_DIR" ] && EASYRSA_DIR="$EXISTING_PKI_DIR"
    else
      report_existing_install
      warn "这台机器上的 OpenVPN 看起来是别的脚本装的（目录布局与本脚本不同）。"
      warn "直接继续会重写 server.conf；若同时换了 tls-crypt 密钥，既有客户端会全部失效。"
      die "为避免破坏既有安装，请显式选择一种方式后重跑：
    sudo bash $0 --adopt    采纳既有安装（推荐）：复用 PKI 与 tls 密钥，只补面板需要的指令，不替换 server.conf
    sudo bash $0 --force    备份后重写 server.conf（仍复用 PKI 与 tls 密钥）"
    fi
  fi

  # 交互式补充（--auto 时全部跳过）
  if [ "$AUTO_INSTALL" != "1" ]; then
    step "安装选项（直接回车使用默认值）"
    if [ -z "$SERVER_ADDR" ]; then
      local guess; guess="$(detect_public_ip || true)"
      read -r -p "公网 IPv4 地址或域名 [${guess:-}]: " ans
      SERVER_ADDR="${ans:-$guess}"
    fi
    read -r -p "协议 (1=UDP 推荐 / 2=TCP) [1]: " ans
    case "$ans" in 2|tcp|TCP) VPN_PROTO="tcp" ;; *) VPN_PROTO="udp" ;; esac
    read -r -p "监听端口 [${VPN_PORT}]: " ans; VPN_PORT="${ans:-$VPN_PORT}"
    read -r -p "首个客户端名称 [${CLIENT_NAME}]: " ans; CLIENT_NAME="${ans:-$CLIENT_NAME}"
    read -r -p "客户端 DNS [${DNS1}]: " ans; DNS1="${ans:-$DNS1}"
    if [ "$PANEL_ENABLE" = "1" ]; then
      read -r -p "是否安装 Web 管理面板？(Y/n): " ans
      case "$ans" in [nN]*) PANEL_ENABLE=0 ;; esac
    fi
  else
    [ -n "$SERVER_ADDR" ] || SERVER_ADDR="$(detect_public_ip || true)"
  fi

  [ -n "$SERVER_ADDR" ] || die "无法确定公网地址，请用 --serveraddr 指定"
  case "$VPN_PROTO" in udp|tcp) ;; *) die "--proto 只能是 udp 或 tcp" ;; esac
  case "$VPN_PORT" in ''|*[!0-9]*) die "--port 必须是数字" ;; esac

  init_pki
  require_openvpn_for_ipv6
  write_server_conf
  enable_ip_forward
  setup_nat
  start_openvpn
  add_client "$CLIENT_NAME"
  deploy_panel
  self_check
  save_state
  print_summary
}

# 仅在直接执行时运行 main；被 source 时只定义函数（供 tests/ 使用）
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
