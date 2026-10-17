#!/usr/bin/env bash
#
# panel-validate.sh —— apply-panel-config 的校验函数库（只定义函数，被 source 时不做任何事）
#
# 背景
# ----
# 「面板网络与安全设置」允许在网页里改监听端口、绑定域名、放行来源与 TLS。
# 面板以**非 root** 用户运行，真正的落地由 root 助手 /usr/local/lib/ovpnmanager/
# apply-panel-config 完成。由于助手拿到的是 root 权限，凡是"值是否合法"的判断都
# 必须在它自己这一侧再做一遍（面板侧的 Python 校验只是第一道，不能当安全边界）。
#
# 为什么单独一个库
# ----------------
# 助手本身要 systemctl / nginx / openssl，没法在开发机上端到端跑；但这些规则
# **拒绝什么** 才是这个特性敢上线的原因，所以它们被写成纯函数，可以脱离 root
# 直接断言。见 tests/test_panel_config.sh。
#
# 约定
# ----
# 所有 *problem 函数：通过时**不输出**任何东西；不通过时把中文原因打印到 stdout。
# 调用方用 `reason="$(fn ...)"; [ -z "$reason" ] || fail "$reason"` 的形态消费。

# ---------------------------------------------------------------------------
# 白名单与上限
# ---------------------------------------------------------------------------
PANEL_ALLOW_MODES="public vpn custom"
PANEL_TLS_MODES="off selfsigned existing"

# 候选文件允许出现的键（顺序无关，缺 ALLOW_CIDRS / TLS_CERT / TLS_KEY 时视为空）
PANEL_CANDIDATE_KEYS="LISTEN_PORT SERVER_NAME ALLOW_MODE ALLOW_CIDRS TLS_MODE TLS_CERT TLS_KEY"
# 必须出现的键
PANEL_REQUIRED_KEYS="LISTEN_PORT SERVER_NAME ALLOW_MODE TLS_MODE"

PANEL_MIN_PORT=1
PANEL_MAX_PORT=65535
PANEL_MAX_SERVER_NAME=253
PANEL_MAX_ALLOW_ENTRIES=32
PANEL_MAX_TLS_FILE_BYTES=1048576

# ---------------------------------------------------------------------------
# 基础：IPv4 / IPv6 / CIDR
# ---------------------------------------------------------------------------
_panel_is_ipv4() { # 内部：点分十进制四段，每段 0-255
  local addr="$1" octet
  case "$addr" in
    ''|*[!0-9.]*) return 1 ;;
  esac
  # 形如 1..2 / .1.2.3 / 1.2.3. 一律拒绝
  case "$addr" in
    .*|*.|*..*) return 1 ;;
  esac
  local old_ifs="$IFS"
  # shellcheck disable=SC2086
  IFS='.'; set -- $addr; IFS="$old_ifs"
  [ "$#" -eq 4 ] || return 1
  for octet in "$@"; do
    [ -n "$octet" ] || return 1
    [ "${#octet}" -le 3 ] || return 1
    # 前导零（010）容易被当成八进制，直接拒绝
    case "$octet" in 0|[1-9]*) ;; *) return 1 ;; esac
    [ "$octet" -le 255 ] 2>/dev/null || return 1
  done
  return 0
}

_panel_is_ipv6() { # 内部：十六进制组 + 至多一个 ::；不支持内嵌 IPv4（极少见于放行规则）
  local addr="$1" group groups=0
  case "$addr" in
    ''|*[!0-9A-Fa-f:]*) return 1 ;;
  esac
  # 三个连续冒号非法；"::" 最多出现一次
  case "$addr" in
    *:::*|':::'*) return 1 ;;
  esac
  local rest="$addr"
  rest="${rest/::/}"
  case "$rest" in
    *::*) return 1 ;;
  esac
  local old_ifs="$IFS"
  IFS=':'
  for group in $addr; do
    [ -n "$group" ] || continue
    [ "${#group}" -le 4 ] || { IFS="$old_ifs"; return 1; }
    groups=$((groups + 1))
  done
  IFS="$old_ifs"
  case "$addr" in
    *::*) [ "$groups" -le 7 ] || return 1 ;;
    *)     [ "$groups" -eq 8 ] || return 1 ;;
  esac
  return 0
}

# CIDR 或单个地址（IPv4/IPv6）是否合法
panel_cidr_problem() { # panel_cidr_problem <cidr|ip>
  local input="$1" addr prefix bits
  [ -n "$input" ] || { echo "放行来源里有空条目"; return 0; }
  case "$input" in
    *[[:space:]]*) echo "放行来源不能包含空白字符：${input}"; return 0 ;;
  esac

  if [ "$input" = "${input%%/*}" ]; then
    addr="$input"; prefix=""
  else
    addr="${input%%/*}"; prefix="${input#*/}"
    case "$prefix" in
      ''|*[!0-9]*) echo "CIDR 前缀长度不合法：${input}"; return 0 ;;
    esac
  fi

  case "$addr" in
    *:*)
      _panel_is_ipv6 "$addr" || { echo "IPv6 地址格式不正确：${input}"; return 0; }
      bits=128
      ;;
    *)
      _panel_is_ipv4 "$addr" || { echo "IPv4 地址格式不正确：${input}"; return 0; }
      bits=32
      ;;
  esac

  if [ -n "$prefix" ]; then
    [ "$prefix" -ge 0 ] 2>/dev/null || { echo "CIDR 前缀长度不合法：${input}"; return 0; }
    [ "$prefix" -le "$bits" ] || { echo "CIDR 前缀长度必须在 0-${bits} 之间：${input}"; return 0; }
  fi
  return 0
}

# ---------------------------------------------------------------------------
# 监听端口
# ---------------------------------------------------------------------------
panel_port_problem() { # panel_port_problem <port> [<ovpn_port>]
  local port="$1" ovpn_port="${2:-}"
  case "$port" in
    ''|*[!0-9]*) echo "监听端口必须是整数（当前 ${port:-空}）"; return 0 ;;
  esac
  # 前导零 / 超长数字会让 [ ] 的比较出现歧义（"0" 本身单独放行，交给范围检查）
  [ "${#port}" -le 5 ] || { echo "监听端口超出范围（1-${PANEL_MAX_PORT}）：${port}"; return 0; }
  case "$port" in
    0) ;;
    0*) echo "监听端口不能有前导零：${port}"; return 0 ;;
  esac
  [ "$port" -ge "$PANEL_MIN_PORT" ] && [ "$port" -le "$PANEL_MAX_PORT" ] \
    || { echo "监听端口必须在 ${PANEL_MIN_PORT}-${PANEL_MAX_PORT} 之间：${port}"; return 0; }
  if [ -n "$ovpn_port" ] && [ "$port" = "$ovpn_port" ]; then
    echo "监听端口不能与 OpenVPN 端口相同（${ovpn_port}），否则 nginx 会抢不到端口"
    return 0
  fi
  return 0
}

# ---------------------------------------------------------------------------
# 绑定域名（server_name）
# ---------------------------------------------------------------------------
panel_server_name_problem() { # panel_server_name_problem <name>
  local name="$1" base label
  [ "$name" = "_" ] && return 0
  [ -n "$name" ] || { echo "绑定域名不能为空（留空表示 _）"; return 0; }
  [ "${#name}" -le "$PANEL_MAX_SERVER_NAME" ] \
    || { echo "绑定域名过长（${#name} > ${PANEL_MAX_SERVER_NAME}）"; return 0; }
  # 空白/引号/分号/$/反引号/路径分隔符等一律拒绝：这些值会进 nginx 配置，
  # 少一个字符的疏忽就等于让面板往 root 的 nginx 配置里写任意指令。
  case "$name" in
    *[[:space:]]*|*/*|*\\*|*\"*|*\'*|*';'*|*'$'*|*'`'*|*'&'*|*'|'*|*'<'*|*'>'*|*'{'*|*'}'*)
      echo "绑定域名包含非法字符：${name}"; return 0 ;;
  esac

  base="$name"
  case "$name" in
    \*.*) base="${name#\*.}" ;;
    \*) echo "通配符只能写成 *.example.com 形式：${name}"; return 0 ;;
  esac

  # 纯 IP 也允许（证书 selfsigned 会用 CN=<IP>）
  case "$base" in
    *:*)
      _panel_is_ipv6 "$base" || { echo "绑定域名既不是合法域名也不是合法 IPv6：${name}"; return 0; }
      return 0
      ;;
    *[0-9].*[0-9])
      _panel_is_ipv4 "$base" && return 0
      ;;
  esac

  # 域名：标签 1-63 位，首尾字母数字，中间允许连字符
  local old_ifs="$IFS"
  IFS='.'
  for label in $base; do
    [ -n "$label" ] || { IFS="$old_ifs"; echo "绑定域名有空标签：${name}"; return 0; }
    [ "${#label}" -le 63 ] || { IFS="$old_ifs"; echo "绑定域名标签过长（>63）：${name}"; return 0; }
    printf '%s' "$label" | grep -qE '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$' \
      || { IFS="$old_ifs"; echo "绑定域名格式不正确（只允许字母/数字/连字符，且不能以连字符开头结尾）：${name}"; return 0; }
  done
  IFS="$old_ifs"
  return 0
}

# ---------------------------------------------------------------------------
# 放行来源
# ---------------------------------------------------------------------------
panel_allow_problem() { # panel_allow_problem <mode> <逗号分隔 CIDR>
  local mode="$1" csv="${2:-}" entry count=0 reason
  case " $PANEL_ALLOW_MODES " in
    *" $mode "*) ;;
    *) echo "放行来源模式只能是 ${PANEL_ALLOW_MODES}（当前 ${mode:-空}）"; return 0 ;;
  esac

  if [ "$mode" != "custom" ]; then
    # public / vpn 由助手自己算，候选里带 CIDR 说明面板逻辑不一致，直接拒绝
    [ -z "$csv" ] || { echo "放行来源为 ${mode} 时不应给出 CIDR 列表"; return 0; }
    return 0
  fi

  [ -n "$csv" ] || { echo "自定义放行来源至少要给一条 CIDR"; return 0; }

  local old_ifs="$IFS"
  # 列表在循环开始时只展开一次，循环体里不再改 IFS（避免歧义）
  IFS=','
  for entry in $csv; do
    # 允许逗号后带空格，逐条去空白
    entry="$(printf '%s' "$entry" | tr -d '[:space:]')"
    [ -n "$entry" ] || continue
    reason="$(panel_cidr_problem "$entry")"
    [ -z "$reason" ] || { IFS="$old_ifs"; echo "$reason"; return 0; }
    count=$((count + 1))
  done
  IFS="$old_ifs"

  [ "$count" -ge 1 ] || { echo "自定义放行来源至少要给一条 CIDR"; return 0; }
  [ "$count" -le "$PANEL_MAX_ALLOW_ENTRIES" ] \
    || { echo "放行来源最多 ${PANEL_MAX_ALLOW_ENTRIES} 条（当前 ${count} 条）"; return 0; }
  return 0
}

# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------
# 证书与私钥是否匹配：0=匹配 1=不匹配 2=无法判断（缺 openssl 或格式不认识）
panel_cert_key_match() { # panel_cert_key_match <cert> <key>
  local cert="$1" key="$2" cm km cp kp
  command -v openssl >/dev/null 2>&1 || return 2

  cm="$(openssl x509 -noout -modulus -in "$cert" 2>/dev/null || true)"
  km="$(openssl rsa -noout -modulus -in "$key" 2>/dev/null || true)"
  if [ -n "$cm" ] && [ "$cm" != "Modulus=" ] && [ -n "$km" ] && [ "$km" != "Modulus=" ]; then
    [ "$cm" = "$km" ] && return 0 || return 1
  fi

  # 回退：EC 证书没有 modulus，改比公钥（覆盖 RSA / ECDSA 两种）
  cp="$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null || true)"
  kp="$(openssl pkey -in "$key" -pubout 2>/dev/null || true)"
  [ -n "$cp" ] && [ -n "$kp" ] || return 2
  [ "$cp" = "$kp" ] && return 0 || return 1
}

panel_tls_problem() { # panel_tls_problem <mode> <cert> <key>
  local mode="$1" cert="${2:-}" key="${3:-}" f size perm rc=0
  case " $PANEL_TLS_MODES " in
    *" $mode "*) ;;
    *) echo "TLS 模式只能是 ${PANEL_TLS_MODES}（当前 ${mode:-空}）"; return 0 ;;
  esac

  if [ "$mode" != "existing" ]; then
    # off 不需要证书；selfsigned 的证书由助手生成在 TLS_DIR 里
    { [ -z "$cert" ] && [ -z "$key" ]; } \
      || { echo "TLS 模式为 ${mode} 时不接受自定义证书路径（由助手生成或无需证书）"; return 0; }
    return 0
  fi

  for f in "$cert" "$key"; do
    [ -n "$f" ] || { echo "existing 模式必须同时给出证书与私钥路径"; return 0; }
    case "$f" in
      /*) ;;
      *) echo "证书/私钥必须是绝对路径：${f}"; return 0 ;;
    esac
    case "$f" in
      *[[:space:]]*) echo "证书/私钥路径不能包含空白字符：${f}"; return 0 ;;
    esac
    [ -f "$f" ] || { echo "证书/私钥文件不存在：${f}"; return 0; }
    [ -r "$f" ] || { echo "证书/私钥文件不可读：${f}"; return 0; }
    [ -s "$f" ] || { echo "证书/私钥文件为空：${f}"; return 0; }
    size="$(stat -c '%s' "$f" 2>/dev/null || echo 0)"
    [ "$size" -le "$PANEL_MAX_TLS_FILE_BYTES" ] \
      || { echo "证书/私钥文件过大（>${PANEL_MAX_TLS_FILE_BYTES} 字节）：${f}"; return 0; }
  done

  # 私钥不能被组/其他用户写（否则本机任何用户都能替换掉服务端私钥）
  perm="$(stat -c '%a' "$key" 2>/dev/null || echo '')"
  case "$perm" in
    *[2367]) echo "私钥不能是组或其他用户可写（当前 ${perm}）：${key}"; return 0 ;;
  esac

  panel_cert_key_match "$cert" "$key" || rc=$?
  case "$rc" in
    0) ;;
    1) echo "证书与私钥不匹配（openssl modulus 比对失败）"; return 0 ;;
    *) echo "无法校验证书与私钥是否匹配（缺少 openssl 或格式不认识），已拒绝"; return 0 ;;
  esac
  return 0
}

# ---------------------------------------------------------------------------
# 候选文件（面板写出的 KEY=VALUE 行）
# ---------------------------------------------------------------------------
panel_candidate_value() { # panel_candidate_value <file> <KEY>
  local file="$1" key="$2" line=""
  [ -f "$file" ] || return 0
  line="$(grep -m1 -E "^[[:space:]]*${key}=" "$file" 2>/dev/null || true)"
  [ -n "$line" ] || return 0
  printf '%s' "${line#*=}"
}

# 整份候选的语义校验。返回空 = 通过。
# 注意：文件层面的检查（存在/普通文件/大小/属主/权限/NUL）在
# apply-validate.sh 的 candidate_file_problem() 里，两个助手共用。
panel_candidate_problem() { # panel_candidate_problem <file> [<ovpn_port>]
  local file="$1" ovpn_port="${2:-}" line key value lineno=0 seen="" reason
  [ -f "$file" ] || { echo "候选配置不存在：${file}"; return 0; }

  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) echo "候选配置第 ${lineno} 行不是 KEY=VALUE 形式：${line}"; return 0 ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"

    case "$key" in
      ''|*[!A-Z0-9_]*)
        echo "候选配置第 ${lineno} 行的键名不合法：${key}"; return 0 ;;
    esac
    case " $PANEL_CANDIDATE_KEYS " in
      *" $key "*) ;;
      *) echo "候选配置包含未知键：${key}"; return 0 ;;
    esac
    case " $seen " in
      *" $key "*) echo "候选配置里重复出现键：${key}"; return 0 ;;
    esac
    seen="${seen} ${key}"

    # 值里出现会破坏 shell / nginx 语法的字符一律拒绝。
    # 助手只做 IFS='=' 切分、绝不 eval，这条是额外的纵深防御。
    case "$value" in
      *[\$\`\\\"\'\;\&\|\<\>]*)
        echo "候选配置的值包含不允许的字符（键 ${key}）"; return 0 ;;
    esac
  done < "$file"

  for key in $PANEL_REQUIRED_KEYS; do
    case " $seen " in
      *" $key "*) ;;
      *) echo "候选配置缺少必需的键：${key}"; return 0 ;;
    esac
  done

  local listen_port server_name allow_mode allow_cidrs tls_mode tls_cert tls_key
  listen_port="$(panel_candidate_value "$file" LISTEN_PORT)"
  server_name="$(panel_candidate_value "$file" SERVER_NAME)"
  allow_mode="$(panel_candidate_value "$file" ALLOW_MODE)"
  allow_cidrs="$(panel_candidate_value "$file" ALLOW_CIDRS)"
  tls_mode="$(panel_candidate_value "$file" TLS_MODE)"
  tls_cert="$(panel_candidate_value "$file" TLS_CERT)"
  tls_key="$(panel_candidate_value "$file" TLS_KEY)"

  reason="$(panel_port_problem "$listen_port" "$ovpn_port")"; [ -z "$reason" ] || { echo "$reason"; return 0; }
  # 绑定域名允许留空（等价于 _）
  [ -n "$server_name" ] || server_name="_"
  reason="$(panel_server_name_problem "$server_name")"; [ -z "$reason" ] || { echo "$reason"; return 0; }
  reason="$(panel_allow_problem "$allow_mode" "$allow_cidrs")"; [ -z "$reason" ] || { echo "$reason"; return 0; }
  reason="$(panel_tls_problem "$tls_mode" "$tls_cert" "$tls_key")"; [ -z "$reason" ] || { echo "$reason"; return 0; }
  return 0
}

# ---------------------------------------------------------------------------
# panel.state：助手成功落地后写下的"当前生效值"
# ---------------------------------------------------------------------------
panel_state_value() { # panel_state_value <file> <KEY>
  local file="$1" key="$2" line=""
  [ -f "$file" ] || return 0
  line="$(grep -m1 -E "^[[:space:]]*${key}=" "$file" 2>/dev/null || true)"
  [ -n "$line" ] || return 0
  line="${line#*=}"
  line="${line#\"}"; line="${line%\"}"
  printf '%s' "$line"
}

# 把 state 文件里的若干键值安全写回（值只允许 A-Za-z0-9._:/,*=-）。
# 越界字符写成 __REJECTED__ 这种显眼标记而不是静默丢弃：值本来就该先过
# panel_candidate_problem，真出现说明上游有 bug，安装脚本读到它会退回默认值。
panel_write_state() { # panel_write_state <file> <KEY> <VALUE> [<KEY> <VALUE> ...]
  local file="$1"; shift
  local tmp key value
  tmp="${file}.new.$$"
  {
    printf '# 由 ovpn-oneclick 的 apply-panel-config 写入，记录面板当前的网络/安全设置。\n'
    printf '# 安装脚本（重装 / --update）渲染 nginx 时**优先读本文件**，因此升级不会\n'
    printf '# 把用户在面板里改过的端口/域名/放行来源/TLS 冲回默认值。请勿手工编辑。\n'
    printf 'PANEL_APPLIED_AT="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    while [ "$#" -ge 2 ]; do
      key="$1"; value="$2"; shift 2
      case "$value" in
        *[!A-Za-z0-9._:/,*=-]*) value="__REJECTED__" ;;
      esac
      printf '%s="%s"\n' "$key" "$value"
    done
  } > "$tmp"
  chmod 0644 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$file"
}

# ---------------------------------------------------------------------------
# 新地址（面板切端口/开 TLS 之后给前端展示"请访问新地址"）
# ---------------------------------------------------------------------------
panel_url_build() { # panel_url_build <tls_mode> <port> <web_name> <host>
  local tls_mode="$1" port="$2" web_name="$3" host="$4"
  local scheme="http"
  [ "$tls_mode" = "off" ] || scheme="https"
  # 默认端口不写进 URL，避免 https://h:443/ 这种别扭形态
  if { [ "$scheme" = "http" ] && [ "$port" = "80" ]; } \
     || { [ "$scheme" = "https" ] && [ "$port" = "443" ]; }; then
    printf '%s://%s/%s/' "$scheme" "$host" "$web_name"
  else
    printf '%s://%s:%s/%s/' "$scheme" "$host" "$port" "$web_name"
  fi
}

# 面板没有绑定域名时，用本机第一个非回环地址拼 URL
panel_default_host() { # panel_default_host <server_name>
  local name="$1" ip=""
  case "$name" in
    ""|"_")
      ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
      [ -n "$ip" ] || ip="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
      [ -n "$ip" ] || ip="127.0.0.1"
      printf '%s' "$ip"
      ;;
    \*.*) printf '%s' "${name#\*.}" ;;
    *)    printf '%s' "$name" ;;
  esac
}
