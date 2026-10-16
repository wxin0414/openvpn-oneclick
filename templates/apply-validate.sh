#!/usr/bin/env bash
#
# apply-validate.sh —— apply-config 的校验函数库（只定义函数，被 source 时不执行任何东西）
#
# 拆出来的原因：这几条规则是整个"面板可改系统配置"特性的安全边界，必须能单独测试。
# 助手以 root 运行、又依赖 systemctl/nginx，没法在开发机上端到端跑；但校验逻辑是纯函数，
# 可以脱离 root 直接断言。见 tests/test_apply_config.sh。

# 托管块标记（必须与面板端 build_candidate 生成的完全一致）
MARK_BEGIN='# >>> ovpn-oneclick managed (do not edit by hand) >>>'
MARK_END='# <<< ovpn-oneclick managed <<<'

# 托管块内允许出现的指令名
# 注意 fast-io 与 explicit-exit-notify 只对 UDP 有效：它们必须由面板托管，
# 否则在面板里把 proto 从 udp 改成 tcp 之后，配置里会留下过期的 UDP 专用指令。
MANAGED_ALLOW="port proto server topology mssfix sndbuf rcvbuf keepalive client-to-client max-clients duplicate-cn block-ipv6 push fast-io explicit-exit-notify"

# 允许出现在 push 里的前缀
# ifconfig-ipv6 是 --block-ipv6 方案需要的（给客户端一个"看着合法"的 IPv6 配置，
# 再把 IPv6 默认路由拉进隧道后由服务端黑洞掉）
PUSH_ALLOW="dhcp-option redirect-gateway route sndbuf rcvbuf ifconfig-ipv6"

# 托管块内的指令名（小写，去重；忽略注释行）
# 注意必须跳过注释：安装脚本自己就会在托管块里写几行说明（以 # 开头）。
# 若不跳过，$1 会是 "#"，于是助手会把安装脚本自己生成的块判成"含未授权指令"，
# 结果是每台新装机在面板里第一次保存就被拒绝。
managed_directives() {
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
    $0==b {inblk=1; next}
    $0==e {inblk=0; next}
    inblk && NF {
      if ($1 ~ /^[#;]/) next
      gsub(/^[ \t]+|[ \t]+$/,"",$1); print tolower($1)
    }
  ' "$1" | sort -u
}

# 托管块之外、去掉注释与空行后的指令行（排序去重）
nonmanaged_lines() {
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
    $0==b {inblk=1; next}
    $0==e {inblk=0; next}
    inblk {next}
    /^[ \t]*#/ {next}
    /^[ \t]*$/ {next}
    {gsub(/^[ \t]+|[ \t]+$/,""); print}
  ' "$1" | sort -u
}

# 托管块内所有 push 行（已去掉前导空白）
managed_push_lines() {
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
    $0==b {inblk=1; next}
    $0==e {inblk=0; next}
    inblk && tolower($1)=="push" {gsub(/^[ \t]+/,""); print}
  ' "$1"
}

# 候选在托管块之外引入的新指令（应为空 = 合法）
unauthorized_extra() { # unauthorized_extra <当前配置> <候选配置>
  comm -13 <(nonmanaged_lines "$1") <(nonmanaged_lines "$2") || true
}

# 面板"退役"被自己接管的旧行时加的前缀（必须与
# backend/app/services/server_conf.py 的 RETIRED_PREFIX 逐字一致）
RETIRED_PREFIX="# [ovpn-oneclick retired] "

# 该行是否被原样保留成了退役注释
retired_as_comment() { # retired_as_comment <候选文件> <原行>
  grep -qF -- "${RETIRED_PREFIX}${2}" "$1"
}

# 候选相对当前配置**移除**的指令行（注释不算指令）
#
# 为什么需要这个：单靠 unauthorized_extra（comm -13）只能发现"新增"，
# 发现不了"偷偷删掉一行"——比如把 crl-verify 删掉就等于关掉了证书吊销，
# 或者删掉 management 让面板自己的在线状态失效。面板只被允许把接管的旧行
# 注释掉（退役），不允许让任何既有指令凭空消失。
removed_lines() { # removed_lines <当前配置> <候选配置>
  comm -23 <(nonmanaged_lines "$1") <(nonmanaged_lines "$2") || true
}

# 一次性校验托管块：指令名白名单 + push 内容白名单。
# 不合法时把原因打印到 stdout 并返回非 0；合法则无输出、返回 0。
validate_managed_block() { # validate_managed_block <配置文件>
  local file="$1" d line payload
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    case " $MANAGED_ALLOW " in
      *" $d "*) ;;
      *) echo "托管块里出现未授权指令：${d}"; return 1 ;;
    esac
  done < <(managed_directives "$file")

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    payload="${line#*push}"
    payload="${payload#"${payload%%[![:space:]]*}"}"
    if ! validate_push "$payload"; then
      echo "托管块里的 push 内容不被允许：${line}"
      return 1
    fi
  done < <(managed_push_lines "$file")

  return 0
}

# push 内容白名单校验
validate_push() { # validate_push <含引号或不含引号的 payload>
  local payload="$1" first rest
  payload="${payload%\"}"; payload="${payload#\"}"
  first="${payload%% *}"
  case " $PUSH_ALLOW " in
    *" $first "*) ;;
    *) return 1 ;;
  esac
  rest="${payload#"$first"}"
  rest="${rest# }"
  case "$first" in
    dhcp-option)
      # 只允许 dhcp-option DNS <地址>
      # shellcheck disable=SC2086
      set -- $rest
      [ "${1:-}" = "DNS" ] || return 1
      [ -n "${2:-}" ] || return 1
      printf '%s' "$2" | grep -qE '^[0-9a-fA-F:.]{3,45}$' || return 1
      ;;
    sndbuf|rcvbuf)
      printf '%s' "$rest" | grep -qE '^[0-9]{1,8}$' || return 1
      ;;
    route)
      printf '%s' "$rest" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}( [0-9]{1,3}(\.[0-9]{1,3}){3})?$' || return 1
      ;;
    ifconfig-ipv6)
      # 形如 fd15:53b6:dead::2/64 [fd15:53b6:dead::1]
      printf '%s' "$rest" | grep -qE '^[0-9a-fA-F:]{2,45}/[0-9]{1,3}( [0-9a-fA-F:]{2,45})?$' || return 1
      ;;
    redirect-gateway)
      printf '%s' "$rest" | grep -qE '^[A-Za-z0-9! -]*$' || return 1
      ;;
  esac
  return 0
}

mask_to_cidr() { # 255.255.255.0 -> 24（非法掩码输出空）
  local mask="$1" bits=0 n
  local IFS=.
  for n in $mask; do
    case "$n" in
      255) bits=$((bits+8)) ;; 254) bits=$((bits+7)) ;; 252) bits=$((bits+6)) ;;
      248) bits=$((bits+5)) ;; 240) bits=$((bits+4)) ;; 224) bits=$((bits+3)) ;;
      192) bits=$((bits+2)) ;; 128) bits=$((bits+1)) ;; 0) ;;
      *) echo ""; return 0 ;;
    esac
  done
  echo "$bits"
}

server_cidr() { # server_cidr <配置文件> -> "10.8.44.0/24" 或空
  local line net mask bits
  line="$(grep -E '^[[:space:]]*server[[:space:]]' "$1" 2>/dev/null | tail -1 || true)"
  [ -n "$line" ] || { echo ""; return 0; }
  # shellcheck disable=SC2086
  set -- $line
  net="${2:-}"; mask="${3:-255.255.255.0}"
  [ -n "$net" ] || { echo ""; return 0; }
  bits="$(mask_to_cidr "$mask")"
  if [ -n "$bits" ]; then echo "${net}/${bits}"; else echo "$net"; fi
}
