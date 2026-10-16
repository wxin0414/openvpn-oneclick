一条命令装好 OpenVPN 服务端与 Web 管理面板；面板源码（FastAPI + Vue 3）就在本仓库内，安装与升级全程不依赖任何第三方项目。

# openvpn-oneclick

> 仓库地址：<https://github.com/wxin0414/openvpn-oneclick>（分支 `main`）

## 项目简介

**这是同一个项目，而不是"安装器 + 一个外挂面板"。** 本仓库既是安装器的源码仓库，也是面板的源码仓库：`install.sh` 一条命令同时装好 **OpenVPN 服务端**和配套的 **Web 管理面板**，而面板的源码就是本仓库里的 `backend/`（FastAPI）与 `frontend/`（Vue 3 及其构建产物 `frontend/ovpnmanager/`）。安装时下载的 `panel/ovpnmanager-panel.tar.gz` 同样是由本仓库自己的源码经 `./pack.sh` 打出来的构建产物——**不会 clone、不会拉取、也不依赖任何第三方项目**。

| 项目 | 说明 |
| --- | --- |
| 形态 | 单个 shell 脚本 `install.sh` + 仓库内自带的安装载荷 |
| 安装内容 | OpenVPN 服务端 + Web 管理面板（一次执行，两样都装好） |
| 面板源码 | 本仓库 `backend/`（FastAPI）+ `frontend/`（Vue 3） |
| 面板载荷 | 本仓库 `panel/ovpnmanager-panel.tar.gz`，由 `./pack.sh` 从上述源码生成 |
| 外部依赖 | 无第三方代码仓库依赖，仅需系统软件包源与出网能力 |
| 许可 | MIT |

> **诚实说明：本脚本至今没有在真实 Linux 服务器上完整跑过一遍端到端流程**——开发环境里没有 Linux 主机，也没有 Docker 与 WSL 可用。因此发行版包名、EasyRSA 路径发现、systemd 单元选择、防火墙分支、面板的 pip / nginx 部署，以及 v1.2.0「服务端设置」的「面板 → sudo → 特权助手」落地通道等环节，**只经过语法检查、单元测试与模板契约测试**，未经实机验证。请先在**可回滚**的测试机上验证，确认无误后再用于生产环境。

## 目录

- [项目简介](#项目简介)
- [一键安装](#一键安装)
- [环境要求](#环境要求)
- [命令行参数](#命令行参数)
- [使用示例](#使用示例)
- [安装脚本做了什么](#安装脚本做了什么)
- [在已装有 OpenVPN 的机器上安装](#在已装有-openvpn-的机器上安装)
- [升级](#升级)
- [IPv6 泄漏防护](#ipv6-泄漏防护)
- [管理面板](#管理面板)
- [服务端设置（在面板里改）](#服务端设置在面板里改)
- [目录与文件位置](#目录与文件位置)
- [项目结构](#项目结构)
- [本次修复](#本次修复)
- [v1.2.0 新增](#v120-新增)
- [验证状态与已知局限](#验证状态与已知局限)
- [安装后须知](#安装后须知)
- [故障排查](#故障排查)
- [自包含与上游项目](#自包含与上游项目)
- [许可证与致谢](#许可证与致谢)

## 一键安装

全自动安装（使用默认值）：

```bash
wget -O install.sh https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh
sudo bash install.sh --auto
```

交互式安装（会依次询问公网 IP、协议、端口、首个客户端名称、是否安装面板等）：

```bash
sudo bash install.sh
```

安装完成后脚本会打印一次性的面板登录凭据，请及时保存。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | 带 systemd 的 Linux |
| 权限 | 必须以 root 运行（`sudo`） |
| 发行版 | Debian、Ubuntu；RHEL 系（CentOS / Rocky / AlmaLinux / Fedora） |
| 网络 | 需要出网能力（安装系统软件包、下载面板载荷） |
| 地址 | 一个公网 IPv4 地址或域名（默认自动探测，可用 `--serveraddr` 覆盖） |
| 端口 | OpenVPN 默认监听 **UDP 1194**；面板通过 **HTTP 80** 对外提供服务 |

## 命令行参数

```text
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
```

安装完成后，随时可以再次运行同一个脚本进行客户端管理，也可以直接使用 Web 面板。

## 使用示例

```bash
# 1) 全自动安装，全部使用默认值
sudo bash install.sh --auto

# 2) 使用 TCP + 443 端口，并把首个客户端命名为 myphone
sudo bash install.sh --proto tcp --port 443 --clientname myphone

# 3) 新增一个客户端并导出 .ovpn
sudo bash install.sh --addclient laptop

# 4) 只装 OpenVPN、不装面板，并启用分流模式
sudo bash install.sh --auto --no-panel --split-tunnel --route 192.168.1.0/24

# 5) 列出已有客户端 / 吊销某个客户端
sudo bash install.sh --listclients
sudo bash install.sh --revokeclient laptop

# 6) 开启 IPv6 泄漏防护
sudo bash install.sh --auto --block-ipv6
```

## 安装脚本做了什么

1. **识别发行版并安装依赖包**：openvpn、easy-rsa、openssl、nginx、python3、iptables、rsync、logrotate。
2. **用 EasyRSA 构建 PKI**：CA、服务端证书、CRL，并生成 tls-crypt 密钥与 DH 参数。若机器上已经存在 PKI 与 tls 密钥，则**只复用、不重建**（见下一节）。
3. **写出 OpenVPN 服务端配置**，其中几处细节值得说明：
   - 默认 `proto udp`。在有丢包的链路上 TCP-over-TCP 会出现严重的性能退化，因此 TCP 需要显式通过 `--proto tcp` 启用。
   - 加密与完整性使用 `tls-crypt`（不是 `tls-auth`）；`data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305`；`auth SHA256`。
   - 性能调优：向客户端推送 `sndbuf` / `rcvbuf` 384KB、`mssfix 1420`，UDP 下启用 `fast-io`。
   - `fast-io` 与 `explicit-exit-notify` 都**只对 UDP 有效**，因此 `--proto tcp` 时这两个选项都不会写进配置（服务端与客户端 `.ovpn` 同理）。
   - DNS 推送：全局代理模式下推送 `--dns1` / `--dns2`（默认 `8.8.8.8` / `8.8.4.4`）；**分流模式下默认不推送任何 DNS**（原因见「本次修复」一节）。
   - 面板所依赖的三组指令：`management 127.0.0.1 7505`、`status ... status-version 3`、`client-config-dir` 与 `crl-verify`。
4. **开启转发与 NAT**：启用 `net.ipv4.ip_forward` 并配置 masquerade。若系统上 firewalld 处于活动状态则走 firewalld，否则使用 iptables，并额外安装一个自己的 systemd 单元来保证规则持久化。
5. **启动 OpenVPN 服务并签发首个客户端证书**，写出 `/etc/openvpn/server/client/<名称>.ovpn`。该文件内含客户端私钥，请及时将其移出服务器再使用。
6. **部署面板**：创建专用的非 root 系统用户、Python 虚拟环境、nginx 站点（同源 `/api` 反向代理，使浏览器不会发出 CORS 预检请求，并开启 gzip）、加固过的 systemd 单元、日志轮转，以及一个受限的 `sudo` 包装脚本——面板因此只能对本机这一个 OpenVPN 服务做启动 / 停止 / 重启，而不是以 root 身份运行。
7. **生成随机 `SECRET_KEY` 与随机管理员密码**，打印一次登录凭据，最后执行一次自检。

## 在已装有 OpenVPN 的机器上安装

这是把两个项目拼接起来时最容易出事的地方，所以单独说明。

**风险在哪。** 如果这台机器上的 OpenVPN 是别的脚本装的（`hwdsl2/openvpn-install`、`Nyr/openvpn-install`，或者博客里的 `centos7-install-openvpn-v2.sh`），它的目录布局是：

```text
/etc/openvpn/server/{server.conf, easy-rsa/, crl.pem, tc.key}
```

注意 tls-crypt 密钥在那里叫 **`tc.key`**，而本脚本默认用 `ta.key`。假如安装脚本不加判断地生成一份新的 `ta.key` 并重写 `server.conf`，服务端的 tls-crypt 密钥就换了，**所有既有客户端的 `.ovpn` 会立刻全部连不上**——客户端带的还是旧密钥，握手阶段就被丢掉。这类故障现场看起来像"服务器挂了"，实际起因只是两边密钥不再匹配。

**现在的做法。** 安装流程一开始就无条件探测机器上是否已有 OpenVPN（在 `/etc/openvpn/server` 与 `/etc/openvpn` 下寻找 `server.conf` / `*.conf`、各家命名的 tls 密钥 `tc.key`、`tls-crypt.key`、`ta.key`、`tls-auth.key`，以及 `easy-rsa/pki`），并且：

- **始终复用既有的 tls 密钥与 PKI**——不会生成第二套 PKI，也不会替换 tls 密钥；
- 如果 `server.conf` 存在且不是本脚本创建的，**默认拒绝继续**，并提示你在 `--adopt` 与 `--force` 之间明确二选一；
- 如果 `server.conf` 正是本脚本此前创建的（通过 `/etc/ovpn-oneclick/state.conf` 里记录的 `SERVER_CONF` 比对确认），则按**重装 / 修复**处理，不需要任何标志。

三种分支的差别：

| 方式 | 对 `server.conf` 做什么 | 对 PKI 与 tls 密钥做什么 |
| --- | --- | --- |
| `--adopt`（推荐） | 不替换：只把面板需要的指令（`management`、`status`、`status-version`、`client-config-dir`、`crl-verify`）**缺哪补哪地追加**到文件末尾，追加前先备份为 `server.conf.bak.<时间戳>` | 全部复用 |
| `--force` | 备份为 `server.conf.bak.<时间戳>` 后整份重写 | 全部复用，因此既有客户端不会失效 |
| 不带标志 | 不动，直接报错退出 | 不动 |

`--adopt` 还会顺带对齐路径与参数：服务实例名改用既有配置的文件名（否则 systemd 加载不到那份配置），CCD 目录优先沿用 `server.conf` 里已经声明的 `client-config-dir`，CRL 与客户端导出目录也按既有布局落位；网段、掩码、协议与端口则从既有 `server.conf` 里读出（例如对方用的是 `10.8.0.0/24`，而不是本脚本默认的 `10.8.44.0/24`），供 NAT 规则、nginx 允许网段与面板使用，避免与实际隧道不一致。对方写入的推送路由等定制项一律保持原样，脚本只是读取它们。

相应地：

```bash
# 采纳既有安装（推荐）：复用 PKI 与 tls 密钥，只追加面板需要的指令
sudo bash install.sh --adopt --auto

# 明确允许备份后重写既有 server.conf（仍复用 PKI 与 tls 密钥）
sudo bash install.sh --force --auto

# 本脚本此前装过：直接重跑即可，按重装/修复处理
sudo bash install.sh --auto
```

## 升级

`--check-update` 只读：把本机已安装版本（取自 `/etc/ovpn-oneclick/state.conf`）、当前脚本版本与仓库 `VERSION.txt` 里的远端版本并排打印出来，明确告诉你有无新版本，**不修改任何东西**。`--update` 执行实际升级（两者都需要 root 与出网能力）。

升级的保证是这件事的重点：

- **绝不重新生成 `SECRET_KEY`**，也**绝不重置管理员口令**——`.env` 已存在时原样保留；
- **绝不动 PKI，也绝不动 `server.conf`**：升级路径不初始化证书体系、不重写服务端配置，既有的 tls 密钥与 PKI 一律沿用；若机器上已有 OpenVPN，升级同样会先探测并对齐既有布局（路径、网段、协议、端口）；`server.conf` 里那段面板托管的块（`# >>> ovpn-oneclick managed` … `# <<< ovpn-oneclick managed <<<`）因此也一个字符都不会变——`--update` 只替换面板代码与模板（含特权助手），你在面板里改过的服务端设置原样保留；
- **绝不覆盖你改过的 `.env` 值**：新版本模板里新增、而现有 `.env` 没有的键会被**追加**到文件末尾（沿用模板默认值，并标注由 `--update` 补充），既有键一个都不碰；
- 升级前把数据库与 `.env` 打包备份到 `/var/backups/ovpn-oneclick/panel-<时间戳>.tar.gz`（只含 `backend/.env` 与 `backend/data`，可重建的 `.venv` 不备份），状态文件另存为 `panel-<时间戳>.state.conf`；备份目录里只保留最新 5 份 `.tar.gz`；
- **安装脚本会自我更新，但先下载再校验**：下载到临时文件后先确认首行像 bash 脚本，再用 `bash -n` 做语法校验；校验通过才备份原脚本、写入 `install.sh.new` 并以 `mv` **原子替换**。任何一步失败都放弃自更新，且不影响面板升级。以 `curl | bash` 这类非磁盘文件方式执行时会跳过自更新；
- 已安装版本与关键路径（服务名、`server.conf`、PKI、CCD、客户端导出目录、CRL、tls 密钥、面板路径等）记录在 `/etc/ovpn-oneclick/state.conf`。

两条命令的升级流程：

```bash
# 1) 先看有没有新版本（不改动任何东西）
sudo bash install.sh --check-update

# 2) 确认后升级
sudo bash install.sh --update
```

## IPv6 泄漏防护

选项 `--block-ipv6`，**默认关闭**，属于可选的加固项。

如果客户端所在网络是双栈的，它会同时拥有一条 IPv6 默认路由；而这条 VPN 只承载 IPv4，于是访问双栈站点时，客户端的 IPv6 流量可能绕开隧道直接从本地出口出去——这就是 IPv6 泄漏。开启该选项后，安装脚本按 OpenVPN 手册中 `--block-ipv6` 一节给出的做法处理，共三步：

1. 给客户端推一份"看着合法"的 IPv6 参数：`push "ifconfig-ipv6 fd15:53b6:dead::2/64 fd15:53b6:dead::1"`；
2. 用 `push "redirect-gateway ipv6"` 把客户端的 IPv6 默认路由一并拉进隧道；
3. 服务端启用 `block-ipv6`，对收到的 IPv6 报文回以 `no-route`，从而真正切断 IPv6 出网。

需要明确的是：它**只是屏蔽 IPv6，并不提供 IPv6**——客户端不会因此获得可用的 IPv6 互联网访问，只是不再泄漏。另外，`block-ipv6` 是 OpenVPN **2.4 及以上**版本才有的选项，旧版本上不要启用。

```bash
sudo bash install.sh --auto --block-ipv6

# 与分流模式一起使用
sudo bash install.sh --auto --split-tunnel --route 192.168.1.0/24 --block-ipv6
```

## 管理面板

面板是一个 **FastAPI + Vue 3 + Element Plus** 的 Web 界面，访问地址：

```text
http://<服务器地址>/ovpnmanager/
```

**默认只允许 VPN 客户端网段访问**（即 `10.8.44.0/24`）。也就是说，你需要先以客户端身份连上 VPN 才能打开面板，而不是从公网直接访问。面板进程本身只监听 `127.0.0.1:8000`，对外由 nginx 提供 HTTP 80 服务并同源反代 `/api`。

功能一览：

| 功能 | 说明 |
| --- | --- |
| 客户端管理 | 新增 / 编辑 / 删除客户端 |
| 证书操作 | 签发、吊销、导出 `.ovpn` |
| 在线列表 | 查看当前在线客户端 |
| 强制下线 | 断开指定客户端 |
| 固定 IP 与路由 | 按客户端配置（CCD） |
| 服务控制 | 启动 / 停止 / 重启 OpenVPN 服务 |
| 服务端设置 | 在面板里改 `server.conf` 的托管块：端口、协议、DNS、路由、IPv6 屏蔽、性能参数、客户端开关、网段（见下一节） |
| 审计日志 | 记录登录、上下线、强制断开等事件，支持条件筛选与分页 |
| 仪表盘 | 概览视图 |
| 用户与口令 | Token 认证、登录限速、自助修改口令 |

证书签发、吊销等操作以**后台任务**方式执行，前端轮询进度，因此一次耗时的 EasyRSA 运行不会把浏览器请求拖到超时。

**技术栈**：后端 FastAPI + SQLAlchemy + Pydantic v2 + SQLite；前端 Vue 3 + Vite + Element Plus + Axios。面板整体时间为 Asia/Shanghai，当地时间格式化仅在前端呈现。

## 服务端设置（在面板里改）

v1.2.0 起，改服务端参数**不再需要重跑 `install.sh` 带标志**。面板多了一个 **服务端设置** 页面：

```text
http://<服务器地址>/ovpnmanager/settings
```

它编辑的是 `server.conf` 里的**托管块**（布局见下文），点「保存并应用」后由 root 特权助手校验并落地。相关接口是 `GET` / `PATCH /api/settings/server` 与 `POST /api/settings/reexport-all`，三者都要求超级管理员。

### 能改什么

| 页面分组 | 项目 | 写入的指令 |
| --- | --- | --- |
| 基本 | 监听端口（1-65535） | `port` |
| 基本 | 协议 `udp` / `tcp` | `proto`（UDP 时附带生成 `fast-io` 与 `explicit-exit-notify 1`；改成 TCP 时这两条不再生成） |
| 隧道与路由 | 全局代理 / 分流 | 全局：`push "redirect-gateway def1 bypass-dhcp"`；分流：按列表逐条 `push "route <网段> <掩码>"` |
| 隧道与路由 | 分流路由 | 同上（只在分流模式生效） |
| 隧道与路由 | 屏蔽 IPv6 | 三行：两条 `push` + 服务端 `block-ipv6`（原因见下文） |
| DNS | 推送 DNS（最多 2 个，IPv4 / IPv6 均可） | `push "dhcp-option DNS <地址>"` |
| 性能 | `mssfix` | `mssfix` |
| 性能 | `sndbuf` / `rcvbuf` | `sndbuf` / `rcvbuf`（服务端标量） |
| 性能 | `keepalive` | `keepalive <间隔> <超时>`，形如 `10 120` |
| 客户端 | 客户端互访 | `client-to-client` |
| 客户端 | 最大连接数 | `max-clients`（关闭开关则不下发这条） |
| 客户端 | 允许同名 CN | `duplicate-cn`（建议保持关闭） |
| 网段 | VPN 网段 / 子网掩码 / `topology` | `server <网段> <掩码>` 与 `topology`（`subnet` / `net30` / `p2p`） |

接口侧的校验才是准的（不通过时返回 400，并给出具体的中文原因）：

- `port` 必须是 1-65535 的整数；`proto` 只能是 `udp` / `tcp`（不开放 `udp6` / `tcp6`）；
- `dns_servers` 最多 2 个，且必须是合法 IPv4 / IPv6 地址；
- `tunnel_mode` 只能是 `full` / `split`；`full` 模式下提交的 `routes` 会被忽略，并在返回文案里说明；
- `routes` 必须是合法 CIDR；
- `mssfix` 为 0-1500；`sndbuf` / `rcvbuf` / `max_clients` 为 0-10485760 的整数或 `null`；
- `keepalive` 必须形如 `"10 120"`（两个整数）或 `null`；
- `network` 必须是网段地址（写了主机位会被明确拒绝，例如 `10.9.0.7` + `255.255.0.0` 会报错，而不是被悄悄规范化成 `10.9.0.0`）；掩码必须是连续掩码且前缀长度 `>= 8`；`network` 与 `netmask` 必须匹配；
- `topology` 只能是 `subnet` / `net30` / `p2p`；
- **未提交的项沿用当前值**——`PATCH` 只带发生变化的字段，所以不会把没出现在表单里的项"改没了"；提交只读项或任何未知键一律 400。

`GET /api/settings/server` 返回 `path`、`exists`、`settings`（上表的可编辑项）、`readonly`（只读项）与 `managed_block_present`。

一个值得说明的细节：**`sndbuf` / `rcvbuf` 面板会同时渲染服务端标量与推给客户端的那两条**（`sndbuf 393216` 与 `push "sndbuf 393216"`）。只调服务端一侧的话客户端仍是小缓冲，而吞吐收益主要来自两端一起放大；早期版本这里漏了客户端那两条，保存一次之后就会静默退化。现在由下面这项检查守着，不会再退化。

### 页面里刻意只读的东西

页面底部把这几项列出来仅供查看，不提供编辑：

| 只读项 | 为什么不动它 |
| --- | --- |
| `management` / `status-version` | 面板自己靠它们读在线列表与状态，改错等于把面板的眼睛摘掉 |
| `crl-verify` | 吊销是否生效全靠它指向的文件，改错等于被吊销的证书又能连上 |
| `client-config-dir` | 固定 IP 与按客户端路由的 CCD 目录 |
| tls 模式（`tls-crypt` / `tls-auth`） | 与服务端密钥以及既有客户端绑定 |
| `cipher` / `data-ciphers` / `auth` | 改错会让服务端与既有客户端握手不上；无 AES-NI 机器的回退逻辑也在安装脚本里 |

这些由安装脚本写入、面板按运行时实际情况展示。提交其中任何一项都会被 400 拒绝。

### 改端口 / 协议 / 网段会发生什么

- **端口或协议**：已签发的每一份客户端 `.ovpn` 都带着旧的 `remote` 端口与协议，改完**全部失效**。页面在你点保存时会先弹一次确认（写明这一点）；保存后的结果卡片会标出「客户端配置受影响」，并给出 **重新导出全部客户端** 按钮。该按钮调 `POST /api/settings/reexport-all`，把未吊销、未禁用的客户端一次性重新导出（同步执行，只读 PKI 与 `ta.key`，不跑 easyrsa / openssl），逐个客户端捕获异常并回报成功 / 失败清单；导出的文件仍需你重新分发。
- **VPN 网段 / 掩码**：助手会先删掉旧网段的 MASQUERADE 规则、按新网段重建 NAT 脚本，再把 nginx 站点配置里的第一条 `allow <网段>;` 改成新网段并 `nginx -t` 校验，然后重启 OpenVPN 服务。已有客户端的固定 IP 可能需要同步调整（页面在该分组下也写了同样的警告）。
- 助手在成功路径的最后**一定会重启一次 OpenVPN 服务**并轮询 `is-active`（默认最多等 20 秒）；起不来就整体回滚。也就是说每次保存都会让在线客户端短暂断开一次。

### 安全模型：面板不写系统文件

面板以**非 root 用户**运行，而 `server.conf`、NAT 脚本、nginx 站点配置都归 root。面板**从不直接编辑这些文件**，流程是：

1. 面板把"想改成的样子"渲染成一份**完整候选配置**，写进自己的数据目录（`/opt/ovpnmanager/backend/data/server.conf.candidate`，权限 `0600`）；
2. 面板执行 `sudo -n /usr/local/lib/ovpnmanager/apply-config`——**不带任何参数**。sudoers 里只放行这一条命令，助手自己也检查"参数个数必须为 0"，因此调用者无法指定要改哪个文件；路径全部来自 root 所有的 `/etc/ovpn-oneclick/apply.conf`；
3. 助手以 root 复核候选，备份、原子替换、按需重建 NAT / nginx、重启并等待就绪，失败整体回滚；
4. 面板解析助手 stdout 的 `RESULT=` / `BACKUP=` / `RESTARTED=` / `ROLLED_BACK=` / `NETWORK_CHANGED=` / `REASON=` 再回报页面。助手在默认 90 秒内没有返回时，面板**不会**说"没生效"，而是明确告诉你无法确认配置是否已落地、请查看助手日志与 `systemctl status` 后再重试。

助手执行的三条校验是这件事敢上线的全部理由：

1. **候选文件本身**：必须存在、是普通文件、非空、不超过 128KB、不含 NUL 字节；必须属于 `/etc/ovpn-oneclick/apply.conf` 里声明的面板用户，且**不能是组或其他用户可写**（否则本机任何用户都能投毒）。面板侧按 `0600` 写这份文件。
2. **托管块白名单**：块内出现的指令名必须在白名单里（`port proto server topology mssfix sndbuf rcvbuf keepalive client-to-client max-clients duplicate-cn block-ipv6 push fast-io explicit-exit-notify`），`push` 的载荷还要逐个匹配允许的形态——`dhcp-option DNS <地址>`、`route <网段> [<掩码>]`、`redirect-gateway` 只允许字母数字 / `!` / 空格 / 连字符、`sndbuf` / `rcvbuf` 只允许一个整数、`ifconfig-ipv6 <地址/前缀> [<对端地址>]`。
3. **托管块之外不得引入任何新指令**：助手用 `comm -13`（当前配置的非托管行 vs 候选的非托管行，注释与空行不计）要求结果为空。也就是说面板只能改托管块里的内容，**不可能**把 `script-security 2` + `up /tmp/x.sh`、`plugin`，或者把 `crl-verify` 悄悄改成 `/dev/null` 塞进候选——那等于把"一个被攻破的 Web 应用"直接升级成以 root 执行任意代码。这条规则也正是面板接管旧指令时**把它们注释掉**（`# [ovpn-oneclick retired] proto tcp`）而不删掉的原因：注释行助手跳过、OpenVPN 也忽略，既中和了旧值又不会触发"引入了新指令"。
4. **反向：候选也不得删除既有指令**。只做第 3 条的话，"把 `crl-verify` 整行删掉"这种降级查不出来——删掉它就等于关掉了证书吊销。助手因此再做一次反向比较（`comm -23`），并要求每一个"消失"的行都能在候选里找到对应的**退役注释**（`# [ovpn-oneclick retired] <原行>`）。即：面板只能把接管的旧行注释掉，不能让任何既有指令凭空消失。

同一套规则**在两边各写一份、各跑一遍**：面板侧先 `validate_candidate` 自检，助手侧独立复核，任何一边不通过都不落地。两边分别是 Python 与 Bash、常量是抄过去的，所以另有两项检查专门守着这条缝：

- `python3 tests/check_apply_contract.py` —— 逐字比对托管块标记、15 项托管指令集合、6 个 push 前缀与**退役前缀**；
- `python3 tests/test_block_parity.py` —— 要求 **`install.sh` 写入托管块的每种指令形态，面板都能渲染**。面板每次保存都整块重写托管块，install.sh 放进去而面板不会渲染的指令会在第一次保存后**静默消失**；这个坑已经踩到两次（`ifconfig-ipv6`、推给客户端的 `sndbuf`/`rcvbuf`），所以用集合比对把它钉住。

### `server.conf` 的布局

`server.conf` 被分成"固定部分"与"托管块"两半：

```text
# ===== 由 ovpn-oneclick 1.2.0 生成 =====
# 固定部分：面板不会修改下面这些行。
dev tun
ca ... / cert ... / key ... / dh ...
tls-crypt /etc/openvpn/server/ta.key
data-ciphers ... / auth SHA256
management 127.0.0.1 7505
status ... / status-version 3
client-config-dir ...
crl-verify ...
user nobody

# >>> ovpn-oneclick managed (do not edit by hand) >>>
# 以下设置可在面板的「服务端设置」页面里修改。
port 1194
proto udp
server 10.8.44.0 255.255.255.0
topology subnet
push "dhcp-option DNS 8.8.8.8"
sndbuf 393216 / rcvbuf 393216
mssfix 1420
keepalive 10 120
client-to-client
# <<< ovpn-oneclick managed <<<
```

- 面板**只写**两个标记之间的内容，块外一个字符都不动（上面第 3 条校验保证它做不到）。
- **手工编辑托管块里的行没有意义**：每次保存都会按表单内容整段重新生成，你的改动会被覆盖。
- 若 `server.conf` 不是本安装器创建的（`--adopt` 采纳的、或从别的项目迁移来的），它没有这个块。**第一次保存**时面板会插入托管块，并把与之冲突的既有指令注释成 `# [ovpn-oneclick retired] …`（旧 `port` / `proto` / `server` 以及被托管的 `push` 行），其它不属于面板托管的行（例如别人手写的 `push "block-outside-dns"`）原样保留；助手在替换前先备份 `server.conf`。对这类配置页面会先给出警告——「该 server.conf 不是由本安装器创建…保存时会插入受管块，并注释掉与之冲突的现有指令，使面板的配置生效；保存前会先生成 `.bak` 备份」，提醒你确认改动符合预期。
- 备份文件名是 `<server.conf>.bak.<时间戳>`；助手成功时也会把它回报到页面「最近一次保存结果」的「备份文件」一栏。

### `--block-ipv6` 为什么要写三行

安装脚本的 `--block-ipv6` 与面板的「屏蔽 IPv6」写的是同样三行，缺一不可：

```text
push "ifconfig-ipv6 fd15:53b6:dead::2/64 fd15:53b6:dead::1"
push "redirect-gateway ipv6"
block-ipv6
```

只写服务端那一行 `block-ipv6` **挡不住双栈客户端**：它的 IPv6 默认路由还在自己网卡上，IPv6 流量根本不进隧道，服务端的 `block-ipv6` 自然看不到这些包，也就无从用 ICMPv6 `no-route` 回掉它们。必须先用前两条把客户端的 IPv6 流量拉进隧道（第一条给的是一份不会被真实网络使用的 ULA 地址），服务端的黑洞才有意义。它**只是屏蔽 IPv6，并不提供 IPv6**；`block-ipv6` 是 OpenVPN 2.4+ 的选项，旧版本不要开。

## 目录与文件位置

| 内容 | 路径 |
| --- | --- |
| 面板代码与虚拟环境 | `/opt/ovpnmanager` |
| 面板环境变量（含 `SECRET_KEY`、`DEFAULT_ADMIN_PASSWORD`） | `/opt/ovpnmanager/backend/.env` |
| 面板数据库 | `/opt/ovpnmanager/backend/data/app.db` |
| nginx 站点配置 | `/etc/nginx/conf.d/ovpnmanager.conf`（若存在 `sites-available` 则为 `/etc/nginx/sites-available/ovpnmanager.conf`） |
| 面板 systemd 单元 | `ovpnmanager.service` |
| NAT 持久化 systemd 单元 | `ovpn-oneclick-nat.service` |
| OpenVPN 服务端配置 | `/etc/openvpn/server/server.conf`（RHEL 系没有 `openvpn-server@` 时为 `/etc/openvpn/server.conf`） |
| tls-crypt 密钥 | `/etc/openvpn/server/ta.key`（采纳既有安装时沿用对方的名字，例如 `tc.key`） |
| 客户端 `.ovpn` 输出目录 | `/etc/openvpn/server/client/` |
| 安装状态（已安装版本与关键路径） | `/etc/ovpn-oneclick/state.conf` |
| 特权助手（root 侧，校验并落地服务端设置） | `/usr/local/lib/ovpnmanager/apply-config`（root:root `0755`，同目录还有校验函数库 `apply-validate.sh`） |
| 特权助手的配置（`SERVER_CONF`、服务名、候选文件路径等） | `/etc/ovpn-oneclick/apply.conf`（root:root `0644`，由 `install.sh` 生成） |
| 面板写出的候选配置 | `/opt/ovpnmanager/backend/data/server.conf.candidate`（`0600`，属面板用户） |
| 助手的日志与最近一次结果 | `/etc/ovpn-oneclick/apply.log`、`/etc/ovpn-oneclick/last-apply.conf` |
| `server.conf` 的备份 | `<server.conf>.bak.<时间戳>`（助手每次落地前生成） |
| 升级前的备份 | `/var/backups/ovpn-oneclick/`（`panel-<时间戳>.tar.gz` 与 `panel-<时间戳>.state.conf`，只保留最新 5 份） |

## 项目结构

```text
openvpn-oneclick/
├── install.sh          # 唯一的一键安装入口（OpenVPN 服务端 + 面板）
├── verify.sh           # 部署后逐条复验修复项（第 10 节冒烟测试面板改配置的落地通道）
├── README.md           # 本说明文件
├── VERSION.txt         # 版本号，供 --check-update 读取
├── pack.sh             # 由 backend/ + frontend/ 生成 panel/*.tar.gz
├── templates/          # nginx / systemd / env / sudoers / logrotate 模板
│   ├── nginx.conf / ovpnmanager.service / env.template / sudoers / logrotate
│   ├── apply-config.sh       # 特权助手（root 侧）：校验、备份、原子替换、NAT/nginx 重建、回滚
│   ├── apply-validate.sh     # 助手的校验函数库（纯函数，可脱离 root 单独测试）
│   └── apply.conf.template   # 助手配置模板 → /etc/ovpn-oneclick/apply.conf
├── tests/              # 安装脚本的契约与功能测试
│   ├── check_env_contract.py / check_templates.py / check_payload.sh / test_functions.sh
│   ├── check_apply_contract.py  # 面板（Python）↔ 助手（Bash）的跨组件契约比对
│   └── test_apply_config.sh     # 助手校验规则的单元测试（不需要 root）
├── backend/            # 面板后端源码（FastAPI）
├── frontend/           # 面板前端源码（Vue 3）+ 构建产物 frontend/ovpnmanager/
└── panel/ovpnmanager-panel.tar.gz   # 安装载荷：curl|bash 安装时下载它
```

> **开发者必读（很容易踩的坑）**
>
> - 面板源码就是本仓库的 **`backend/` + `frontend/`**，不再有第二个仓库。
> - `panel/ovpnmanager-panel.tar.gz` 是**构建产物**，由 `./pack.sh` 从 `backend/`、`frontend/ovpnmanager/` 与 `templates/` 打包生成；安装脚本下载并部署的正是这个 tarball。
> - 因此**改完面板源码后必须执行 `./pack.sh` 并把重新生成的 tarball 一起提交**，否则安装脚本仍然发的是旧面板。打包前若前端有改动，请先在 `frontend/` 下执行 `npm ci && npm run build`，因为 `pack.sh` 只接受已存在的构建产物 `frontend/ovpnmanager/index.html`。
> - `verify.sh` 用于**部署后**的逐条复验：`sudo ./verify.sh`。除了第 10 节会真实调用一次特权助手（见「v1.2.0 新增」里的说明），其余检查均为只读——不改配置、不重启服务、不吊销证书。
> - 面板的回归测试是 `backend/tests/test_fixes.py`（140 项断言，不依赖 pytest，需要 `httpx`，用后端虚拟环境跑：`backend/.venv/bin/python backend/tests/test_fixes.py`）。
> - `backend/tests/objective_matrix.py` 把每一条已修复的问题追溯到可执行的断言与源码证据，退出码非 0 表示有目标缺乏证据。
> - `tests/` 下是**安装脚本自身**的契约与功能测试：`check_env_contract.py`、`check_templates.py`、`check_payload.sh`、`test_functions.sh`，以及 v1.2.0 新增的 `check_apply_contract.py`（面板与助手的常量逐字比对）与 `test_apply_config.sh`（助手校验规则的单元测试，不需要 root）。

## 本次修复

本次修掉的主要是几类"不报错、但静默失效"的问题，以及部署中暴露的性能问题。每一项都有可执行断言兜底。

### 速度

| 修复 | 说明 |
| --- | --- |
| 去掉 307 重定向 | 前端列表接口改带尾斜杠。**实测**访问日志 621 条请求里有 168 条是 307 |
| 同源反代 `/api` | nginx 反代消除 CORS 预检（**实测** 96 条 OPTIONS），另显式设置 `max_age` |
| management 连接复用 | 原实现每条命令新建一次 TCP 连接；仪表盘一次请求要建 4 条 |
| `status 3` 只跑一次 | 原实现把同一条 `status 3` 跑了两遍（在线列表 + 状态详情） |
| TTL 缓存 + 熔断 | 只读探测 3 秒内复用；不可达时快速失败，不再串行等 4×3s（最坏 12s，已超过前端 8s 超时） |
| `GET /clients` 恢复只读 | 原实现每次列表都全表加载 + 逐条 commit + 写审计；状态同步移到后台线程 |
| 审计日志加索引 | `created_at` 与 `(action, created_at)`，日志页不再全表扫描排序 |
| SQLite WAL | 开启 WAL + `busy_timeout`，读写不再互相阻塞 |
| 前端按需引入 | Element Plus 按需加载：1.44MB → 830KB（原始体积）；nginx 开启 gzip 后约 250KB |
| 服务状态查询 | `systemctl show` 取代格式化整个 `systemctl status` 输出 |
| 高带宽时延积调优 | `sndbuf` / `rcvbuf` 提升并推送给客户端、`mssfix`、`fast-io`（见「安装脚本做了什么」） |

> **数字的来源要分清**：表中只有"修复前"的数字是**在真实环境量出来的**——307 与 OPTIONS 来自项目自己的 `nohup.out` 访问日志，1.44MB 来自本地构建产物。**"修复后"的体积与请求数并没有在真实服务器上复测**，830KB 是本地构建结果，约 250KB 是开启 gzip 后的估算值。这些结论目前只由单元测试与本地构建支撑，不是线上实测。

### 隧道协议默认改为 UDP

默认模板从 `proto tcp` 改为 **`proto udp`**。TCP over TCP 会把内外两层重传叠加放大，链路一有丢包吞吐就会塌陷，因此 TCP 改为需显式 `--proto tcp` 选择。客户端模板现在**从 `server.conf` 读取** `proto` / cipher / tls 模式，不再写死，以后改协议不需要改代码。无 AES-NI 的机器会回退到 `CHACHA20-POLY1305`。

### 跨权限边界的文件权限

面板以专用的非 root 用户运行，OpenVPN 则以 `nobody` 运行；两者之间文件权限出错时不会有任何报错，只会安静地失灵。现在面板对这几类文件显式 `chmod`：

- **发布的 CRL 强制 `0644`**。OpenVPN 是在降权到 `nobody` **之后**才重新读取 CRL 的：CRL 若是 `0600`，它读不到却也不会报错，结果是**吊销根本不生效**，被吊销的证书照样能连上来。安装脚本这边写入 CRL 时同样按 `0644` 处理。
- **CCD 文件 `0644`**。否则按客户端下发的固定 IP / 路由会被静默忽略。
- **导出的 `.ovpn` 文件 `0600`**。该文件内嵌客户端私钥，默认 umask 下会是 `0644`，同机任何用户都能把它读走。

面板的 systemd 单元同时设置 `UMask=0077`，让面板新建的文件默认私有；必须由 OpenVPN 读取的文件（CRL、CCD）再由代码显式放开。

### 分流模式不再默认推送公共 DNS

`--split-tunnel` 下默认不推送任何 DNS：把 `8.8.8.8` 这类公共 DNS 推给客户端，会让内网域名的解析也被送到公网，内网访问随之失效。确实需要时显式传 `--dns1`。

### 只对 UDP 有效的选项不再写进 TCP 配置

`explicit-exit-notify` 与 `fast-io` 只在 UDP 下有意义，`--proto tcp` 时不再写入服务端配置，客户端 `.ovpn` 里也不会出现。

### 其它正确性与安全修正

- **服务"已停止"不再显示 unknown**：`systemctl status` 对未运行单元返回退出码 3，原实现当成错误抛异常，导致判断 `inactive` 的分支永远执行不到。
- **吊销 / 删除客户端不再重启整个 OpenVPN**：原来会把所有在线用户一起踢下线。现在只让 OpenVPN 重读变化的 CRL，并单独断开被吊销客户端的会话。
- **`OVPNM_DB_PATH` 现在真的生效**：原 `.env-example` 里的这个键与配置字段名不匹配，被静默丢弃，导致面板与 `tls_verify.py` 可能指向不同的库。
- **`AuditLog` 保留旧表名 `auditlog`**：若改名，`create_all` 会新建空表，已有部署的审计历史会被孤立。
- **`/16` 网段不再实例化六万多个地址对象**。
- **时区数据缺失不再导致启动失败**：`zoneinfo` 无数据时退化为固定 UTC+8。
- **`SECRET_KEY` 与管理员口令随机生成**：原仓库把 `SECRET_KEY=qa111-wsx-111-edc-rfv-111` 写在 `.env-example` 里，等于公开签名密钥；现在由安装脚本随机生成并只打印一次。
- **登录限速**（默认 5 分钟内 8 次失败即锁定 5 分钟），并提供自助修改口令接口；启动时自检会把仍在使用的默认值打进日志，`/api/health/diagnostics` 可查询。

## v1.2.0 新增

- **服务端设置页面**：在面板里改 `server.conf` 的托管块（端口、协议、DNS、路由、IPv6 屏蔽、性能参数、客户端开关、网段），不必再重跑安装脚本带标志。详见「服务端设置（在面板里改）」一节。
- **受控的特权助手**：`/usr/local/lib/ovpnmanager/apply-config` + 校验函数库 + `/etc/ovpn-oneclick/apply.conf` + sudoers 里放行的一条**不接受任何参数**的命令，让非 root 的面板能在"只动托管块"的前提下落地配置，失败自动回滚。
- **跨组件契约测试** `tests/check_apply_contract.py`：比对面板（Python）与助手（Bash）两边各写了一份的托管块标记、托管指令集合与 push 前缀，必须逐字一致；任一边改了另一边没改就报错——否则表现是"面板每次保存都被助手拒绝"。
- **助手校验规则的单元测试** `tests/test_apply_config.sh`：把校验逻辑从 root 依赖里拆出来后逐条断言"助手会拒绝什么"，含三个恶意候选样例（托管块外的 `script-security` / `up`、把 `crl-verify` 改成 `/dev/null`）。
- **`verify.sh` 新增第 10 节**：检查助手与它的配置是否存在、属主、权限、语法与 sudoers 授权，并以面板用户身份**无密码调用一次助手**做冒烟测试——候选文件不存在时助手会拒绝并回报 `RESULT=failed`，恰好同时证明 sudo 白名单生效、助手可执行、且它不接受非法输入。助手在**成功应用后会删除候选**，所以正常情况下不会留下待应用的配置；万一存在（说明上次应用失败），第 10 节会检测到并**跳过这次调用**，只做静态检查——一个自检脚本不应该真的去改配置、重启服务。

## 验证状态与已知局限

这一节是刻意保留的，请务必读完再决定是否上生产。

| 环节 | 目前的验证方式 | 是否实机端到端验证 |
| --- | --- | --- |
| 发行版识别与包名（apt / dnf / yum） | 语法检查 + 模板 / 契约测试 | 否 |
| EasyRSA 路径发现与 PKI 初始化 | 语法检查 + 契约测试 | 否 |
| systemd 单元选择（`openvpn-server@` / `openvpn@`） | 模板契约测试 | 否 |
| 防火墙分支（firewalld / iptables）与 NAT 持久化 | 语法检查 + 契约测试 | 否 |
| 面板部署（venv、pip、nginx、sudoers 包装脚本） | 语法检查 + 单元测试 + 模板契约测试 | 否 |
| 服务端设置：面板 → `sudo` → 特权助手（校验、备份、原子替换、NAT / nginx 重建、重启等待、回滚） | `tests/test_apply_config.sh`（纯校验函数）+ `tests/check_apply_contract.py`（跨组件契约）+ `bash -n` 语法检查 | 否 |
| 面板自身逻辑（HTTP 层、management 客户端、CRL/CCD 权限等） | `backend/tests/test_fixes.py` 单元测试 | 否（测试跑在开发机上） |
| "修复后"的性能数字 | 本地构建与代码审查 | 否 |

**本脚本从未在真实 Linux 服务器上执行过完整流程**：开发过程中没有可用的 Linux 主机，也没有 Docker 与 WSL。因此上面这些环节目前**只由语法检查（`bash -n`）、单元测试与模板契约测试覆盖，缺少端到端验证**。

**v1.2.0 的服务端设置落地通道也从未以 root 在真实主机上跑过。**「面板 → `sudo` → `/usr/local/lib/ovpnmanager/apply-config`」这条调用链、助手的备份 / 原子替换 / NAT 与 nginx 重建 / 重启等待 / 失败回滚，全部只在开发机上被**静态地**检查过：`templates/apply-validate.sh` 里的纯校验函数由 `tests/test_apply_config.sh` 逐条断言，面板与助手两边的常量由 `tests/check_apply_contract.py` 逐字比对，两个脚本另做 `bash -n` 语法检查。也就是说**校验逻辑有测试、落地路径没有**——"改配置把服务搞挂"恰恰只会发生在没有测试的那一半。请务必先在可回滚的测试机上跑一次「服务端设置」的保存（改一个无害项即可），确认服务重启后仍为 `active`、`<server.conf>.bak.<时间戳>` 备份已生成、网段变更时 NAT 与 nginx 放行网段同步更新，再用于生产。

建议的做法：

1. 先在一台**可回滚**（快照 / 可重装）的测试机上跑一遍 `sudo bash install.sh --auto`；
2. 用客户端连一次 VPN，确认能出网、能打开 `http://<服务器>/ovpnmanager/`；
3. 跑一次 `sudo ./verify.sh` 逐条复验修复项（第 10 节只在没有待应用候选时才真实调用助手，不会改动配置）；
4. 登录面板，在「服务端设置」里改一个无害项（例如 `keepalive`）并保存，确认服务重启后仍是 `active`、`<server.conf>.bak.<时间戳>` 备份已生成、页面回显的设置与 `server.conf` 里托管块的内容一致；网段变更还要确认 NAT 与 nginx 放行网段同步更新；
5. 再跑一次 `sudo bash install.sh --check-update` / `--update`，确认升级不会动 PKI、`server.conf`（含托管块）、`SECRET_KEY` 与管理员口令；
6. 以上都通过后，才用于生产环境。

## 安装后须知

### 放行云防火墙 / 安全组端口

如果服务器位于云厂商的防火墙或安全组之后，需要手动放行 VPN 端口（默认为 **UDP 1194**），否则客户端无法建立连接。面板走 HTTP 80，同样需要放行。

### 从 VPN 网段之外访问面板

面板允许的来源网段写在 nginx 配置的 `allow` / `deny` 行中。若需要从 VPN 客户端网段之外访问面板，编辑该配置并重载 nginx：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

请注意：`/api/health/diagnostics` 这个诊断接口被**刻意限制为仅 127.0.0.1 可访问**，即使放开了面板来源网段，它也不会对外暴露。

### 修改 VPN 客户端网段

安装阶段 `--network` 只接受网段前三位，掩码固定为 `/24`。v1.2.0 起可以在面板的「服务端设置」页面里改网段、掩码与 `topology`（掩码必须是连续掩码且前缀长度 `>= 8`，网段必须是网段地址），改完助手会重建 NAT 与 nginx 放行网段并重启服务，已有客户端的固定 IP 需要同步调整。

### 忘记面板密码

管理员账号**只在缺失时才会被创建**。因此可以把该账号那一行删掉，再重启面板服务，面板便会按 `/opt/ovpnmanager/backend/.env` 中的 `DEFAULT_ADMIN_PASSWORD` 重新播种。以下命令需要 root：

```bash
sudo python3 -c "import sqlite3; c = sqlite3.connect('/opt/ovpnmanager/backend/data/app.db'); c.execute(\"DELETE FROM users WHERE username='admin'\"); c.commit(); c.close()"
sudo systemctl restart ovpnmanager
```

`.env` 里的 `DEFAULT_ADMIN_PASSWORD` 若已被你改过，重新播种的将是新值。

## 故障排查

| 现象 | 排查方向 |
| --- | --- |
| 客户端能连上但没有网络 | 检查 `net.ipv4.ip_forward` 是否为 `1`、NAT 规则是否存在、以及云防火墙 / 安全组是否放行了出网与相关端口 |
| 客户端一开始就连不上 | 确认云安全组已放行 VPN 端口（默认 **UDP 1194**）；若用 `--proto tcp`，放行的应是 TCP |
| 客户端能连上但被吊销的证书仍可用 | 检查 CRL 权限是否为 `0644`（`0600` 会让 OpenVPN 降权后读不到，吊销静默失效） |
| 面板打不开 | 确认你当前是从 VPN 客户端网段（默认 `10.8.44.0/24`）访问，而不是从公网直接访问；再看 nginx 的 `allow` / `deny` |
| 面板能打开但接口报错 | 面板进程监听 `127.0.0.1:8000`，检查 nginx 的 `/api/` 反代与 `ovpnmanager` 服务状态 |
| OpenVPN 起不来 | 查看日志：`journalctl -u openvpn-server@server` 或 `journalctl -u openvpn@server` |
| 面板起不来 | 查看日志：`journalctl -u ovpnmanager` |
| 升级后仍是旧面板 | 升级依赖仓库里的 `panel/ovpnmanager-panel.tar.gz`；维护者改完面板源码后需要重新执行 `./pack.sh` 并提交该 tarball |

## 自包含与上游项目

**安装过程只访问本仓库。** 本脚本不会访问、clone 或依赖以下两个曾给它带来启发的上游项目：

- `slc14211421/ovpnmanager` —— 管理面板；
- `hwdsl2/openvpn-install` —— OpenVPN 安装器。

面板的源码就在本仓库（`backend/` + `frontend/`），其构建产物 `panel/ovpnmanager-panel.tar.gz` 也由本仓库的 `./pack.sh` 生成并随仓库分发；即便上述项目消失或网络不可达，本安装器仍能正常工作。

本仓库中的 **OpenVPN 服务端安装逻辑是独立实现**：其中的交互风格与命令行设计参考并受启发于 `hwdsl2/openvpn-install` 与 `Nyr/openvpn-install`，但并非逐行照搬。

## 许可证与致谢

本项目以 **MIT** 许可证发布。

- 面板为 **lison** 开发的 `ovpnmanager`（MIT 许可）。
- OpenVPN 服务端安装逻辑为本仓库的独立实现；其交互风格与命令行设计参考并受启发于 `hwdsl2/openvpn-install` 与 `Nyr/openvpn-install`（均为 MIT 许可），在此致谢。
