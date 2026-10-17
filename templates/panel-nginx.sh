#!/usr/bin/env bash
#
# panel-nginx.sh —— nginx 站点配置的唯一渲染实现（被两个调用方共用）
#
#   1. install.sh（安装 / 重装 / --update）：渲染站点配置，**优先读**
#      /etc/ovpn-oneclick/panel.state，所以升级不会把用户在面板里改过的
#      端口 / 域名 / 放行来源 / TLS 冲回默认值；
#   2. root 助手 apply-panel-config（面板里保存「面板设置」时）：用同一份模板
#      和同一个函数重新渲染，保证两条路径写出来的配置逐字节同构。
#
# 为什么必须共用
# --------------
# 如果安装脚本和助手各写一份渲染逻辑，任何一处不一致都会变成"面板里改完好好的，
# 一次升级又回去了"或"升级后 nginx -t 直接失败"。这里只留一份实现，
# 模板 templates/nginx.conf 里的两段托管标记（>>> / <<<）是唯一的结构契约：
#
#   # >>> ovpn-oneclick panel listen (do not edit by hand) >>>
#   ...
#   # <<< ovpn-oneclick panel listen <<<
#   # >>> ovpn-oneclick panel allow (do not edit by hand) >>>
#   ...
#   # <<< ovpn-oneclick panel allow <<<
#
# 标记之外的内容（gzip、location、反代）原样保留，两边都不改。
#
# 用法（环境变量，不接位置参数以外的输入）
# ----------------------------------------
#   PANEL_LISTEN_PORT=8443 \
#   PANEL_SERVER_NAME=example.com \
#   PANEL_ALLOW_MODE=custom PANEL_ALLOW_CIDRS='203.0.113.0/24,127.0.0.1' \
#   PANEL_TLS_MODE=selfsigned \
#   PANEL_TLS_CERT=/etc/ovpn-oneclick/tls/panel.crt \
#   PANEL_TLS_KEY=/etc/ovpn-oneclick/tls/panel.key \
#   PANEL_PORT=8000 WEB_ROOT=/var/www WEB_NAME=ovpnmanager \
#   VPN_CIDR=10.8.44.0/24 \
#   panel_nginx_render /path/templates/nginx.conf /etc/nginx/conf.d/ovpnmanager.conf
#
# 注意：本文件只定义函数，被 source 时不执行任何东西（便于单测与 install.sh 复用）。

PANEL_NGINX_TAG_LISTEN="ovpn-oneclick panel listen"
PANEL_NGINX_TAG_ALLOW="ovpn-oneclick panel allow"

# sed 替换串里的元字符转义（与 install.sh 的 render 同一套规则）
panel_nginx_escape() {
  printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'
}

# 生成托管段的内容行（每行都以 4 空格缩进，与模板其余部分对齐）
panel_nginx_listen_lines() {
  local tls_mode="$1" port="$2" server_name="$3" cert="$4" key="$5"
  if [ "$tls_mode" = "off" ]; then
    printf '    listen %s;\n' "$port"
    printf '    listen [::]:%s;\n' "$port"
    printf '    server_name %s;\n' "$server_name"
    return 0
  fi
  printf '    listen %s ssl;\n' "$port"
  printf '    listen [::]:%s ssl;\n' "$port"
  printf '    server_name %s;\n' "$server_name"
  printf '    ssl_certificate %s;\n' "$cert"
  printf '    ssl_certificate_key %s;\n' "$key"
  # 只留 TLS 1.2/1.3：管理面板不接受已经不再安全的旧协议
  printf '    ssl_protocols TLSv1.2 TLSv1.3;\n'
  printf '    ssl_prefer_server_ciphers off;\n'
  printf '    ssl_session_cache shared:ovpnpanel:1m;\n'
  printf '    ssl_session_timeout 10m;\n'
}

panel_nginx_allow_lines() {
  local mode="$1" csv="$2" vpn_cidr="$3" entry seen=" "
  case "$mode" in
    public)
      printf '    allow 0.0.0.0/0;\n'
      printf '    allow ::/0;\n'
      ;;
    vpn)
      [ -n "$vpn_cidr" ] && printf '    allow %s;\n' "$vpn_cidr"
      printf '    allow 127.0.0.1;\n'
      printf '    allow ::1;\n'
      ;;
    custom)
      local old_ifs="$IFS"
      IFS=','
      for entry in $csv; do
        entry="$(printf '%s' "$entry" | tr -d '[:space:]')"
        [ -n "$entry" ] || continue
        # 同一条重复出现只写一次（nginx 允许重复，但配置会越改越乱）
        case "$seen" in *" $entry "*) continue ;; esac
        seen="${seen}${entry} "
        printf '    allow %s;\n' "$entry"
      done
      IFS="$old_ifs"
      # 本机永远放行：面板自身的健康检查与运维排障都走 127.0.0.1
      case "$seen" in *" 127.0.0.1 "*) ;; *) printf '    allow 127.0.0.1;\n' ;; esac
      case "$seen" in *" ::1 "*) ;; *) printf '    allow ::1;\n' ;; esac
      ;;
    *)
      printf 'panel_nginx_render: 未知的放行模式 %s\n' "${mode:-空}" >&2
      return 1
      ;;
  esac
  # allow/deny 是"首次匹配生效"，deny all 必须最后
  printf '    deny all;\n'
}

# panel_nginx_render <模板> <目标文件>
panel_nginx_render() {
  local template="$1" dest="$2"
  local listen_port="${PANEL_LISTEN_PORT:-}" server_name="${PANEL_SERVER_NAME:-_}"
  local allow_mode="${PANEL_ALLOW_MODE:-vpn}" allow_cidrs="${PANEL_ALLOW_CIDRS:-}"
  local tls_mode="${PANEL_TLS_MODE:-off}" tls_cert="${PANEL_TLS_CERT:-}" tls_key="${PANEL_TLS_KEY:-}"
  local panel_port="${PANEL_PORT:-8000}" web_root="${WEB_ROOT:-/var/www}"
  local web_name="${WEB_NAME:-ovpnmanager}" vpn_cidr="${VPN_CIDR:-}"

  if [ -z "$listen_port" ]; then
    printf 'panel_nginx_render: 缺少 PANEL_LISTEN_PORT\n' >&2
    return 1
  fi
  if [ ! -f "$template" ]; then
    printf 'panel_nginx_render: 模板不存在 %s\n' "$template" >&2
    return 1
  fi
  [ -n "$server_name" ] || server_name="_"
  if [ "$tls_mode" != "off" ] && { [ -z "$tls_cert" ] || [ -z "$tls_key" ]; }; then
    printf 'panel_nginx_render: TLS 模式 %s 需要证书与私钥路径\n' "$tls_mode" >&2
    return 1
  fi

  local tmp="${dest}.new.$$"
  local begin_listen end_listen begin_allow end_allow
  begin_listen="# >>> ${PANEL_NGINX_TAG_LISTEN} (do not edit by hand) >>>"
  end_listen="# <<< ${PANEL_NGINX_TAG_LISTEN} <<<"
  begin_allow="# >>> ${PANEL_NGINX_TAG_ALLOW} (do not edit by hand) >>>"
  end_allow="# <<< ${PANEL_NGINX_TAG_ALLOW} <<<"

  local line state="outside" seen_listen=0 seen_allow=0 block trimmed
  : > "$tmp"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    # 模板里的标记带 4 空格缩进，比较时按去掉前导空白的形式判断，
    # 但写回时保留原缩进（nginx 配置缩进统一，读起来才不别扭）。
    trimmed="${line#"${line%%[![:space:]]*}"}"
    if [ "$trimmed" = "$begin_listen" ]; then
      if ! block="$(panel_nginx_listen_lines "$tls_mode" "$listen_port" "$server_name" "$tls_cert" "$tls_key")"; then
        rm -f "$tmp"; return 1
      fi
      printf '%s\n' "$line" >> "$tmp"
      printf '%s\n' "$block" >> "$tmp"
      state="listen"
      seen_listen=1
      continue
    fi
    if [ "$trimmed" = "$begin_allow" ]; then
      if ! block="$(panel_nginx_allow_lines "$allow_mode" "$allow_cidrs" "$vpn_cidr")"; then
        rm -f "$tmp"; return 1
      fi
      printf '%s\n' "$line" >> "$tmp"
      printf '%s\n' "$block" >> "$tmp"
      state="allow"
      seen_allow=1
      continue
    fi
    if [ "$trimmed" = "$end_listen" ] || [ "$trimmed" = "$end_allow" ]; then
      printf '%s\n' "$line" >> "$tmp"
      state="outside"
      continue
    fi
    if [ "$state" != "outside" ]; then
      # 托管段里的旧内容整段丢弃（由上面的生成行取代）
      continue
    fi

    # 标记之外：只做占位符替换，其余逐字节保留
    line="$(printf '%s' "$line" | sed \
      -e "s|__PANEL_PORT__|$(panel_nginx_escape "$panel_port")|g" \
      -e "s|__PANEL_WEB_PORT__|$(panel_nginx_escape "$listen_port")|g" \
      -e "s|__WEB_ROOT__|$(panel_nginx_escape "$web_root")|g" \
      -e "s|__WEB_NAME__|$(panel_nginx_escape "$web_name")|g" \
      -e "s|__VPN_NETWORK__/__VPN_CIDR__|$(panel_nginx_escape "$vpn_cidr")|g")"
    printf '%s\n' "$line" >> "$tmp"
  done < "$template"

  if [ "$seen_listen" != "1" ] || [ "$seen_allow" != "1" ]; then
    rm -f "$tmp"
    printf 'panel_nginx_render: 模板缺少托管标记（listen=%s allow=%s）\n' \
      "$seen_listen" "$seen_allow" >&2
    return 1
  fi

  # 渲染结果自检：少这几行就说明标记被破坏，宁可不落盘
  local need
  for need in 'listen ' 'deny all;' 'server_name ' 'proxy_pass http://127.0.0.1:'; do
    if ! grep -qF -- "$need" "$tmp"; then
      rm -f "$tmp"
      printf 'panel_nginx_render: 渲染结果缺少必需内容（%s）\n' "$need" >&2
      return 1
    fi
  done

  chmod 0644 "$tmp" 2>/dev/null || true
  if ! mv -f "$tmp" "$dest"; then
    rm -f "$tmp"
    printf 'panel_nginx_render: 无法写入 %s\n' "$dest" >&2
    return 1
  fi
  return 0
}
