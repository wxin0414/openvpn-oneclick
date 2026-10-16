一条命令装好 OpenVPN 服务端与 Web 管理面板；面板源码（FastAPI + Vue 3）就在本仓库内，安装与升级全程不依赖任何第三方项目。

# openvpn-oneclick

> 仓库地址：<https://gitee.com/mycool007/openvpn-oneclick>（分支 `main`）

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

> **诚实说明：本脚本至今没有在真实 Linux 服务器上完整跑过一遍端到端流程**——开发环境里没有 Linux 主机，也没有 Docker 与 WSL 可用。因此发行版包名、EasyRSA 路径发现、systemd 单元选择、防火墙分支、面板的 pip / nginx 部署等环节，**只经过语法检查、单元测试与模板契约测试**，未经实机验证。请先在**可回滚**的测试机上验证，确认无误后再用于生产环境。

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
- [目录与文件位置](#目录与文件位置)
- [项目结构](#项目结构)
- [本次修复](#本次修复)
- [验证状态与已知局限](#验证状态与已知局限)
- [安装后须知](#安装后须知)
- [故障排查](#故障排查)
- [自包含与上游项目](#自包含与上游项目)
- [许可证与致谢](#许可证与致谢)

## 一键安装

全自动安装（使用默认值）：

```bash
wget -O install.sh https://gitee.com/mycool007/openvpn-oneclick/raw/main/install.sh
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
- **绝不动 PKI，也绝不动 `server.conf`**：升级路径不初始化证书体系、不重写服务端配置，既有的 tls 密钥与 PKI 一律沿用；若机器上已有 OpenVPN，升级同样会先探测并对齐既有布局（路径、网段、协议、端口）；
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
| 审计日志 | 记录登录、上下线、强制断开等事件，支持条件筛选与分页 |
| 仪表盘 | 概览视图 |
| 用户与口令 | Token 认证、登录限速、自助修改口令 |

证书签发、吊销等操作以**后台任务**方式执行，前端轮询进度，因此一次耗时的 EasyRSA 运行不会把浏览器请求拖到超时。

**技术栈**：后端 FastAPI + SQLAlchemy + Pydantic v2 + SQLite；前端 Vue 3 + Vite + Element Plus + Axios。面板整体时间为 Asia/Shanghai，当地时间格式化仅在前端呈现。

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
| 升级前的备份 | `/var/backups/ovpn-oneclick/`（`panel-<时间戳>.tar.gz` 与 `panel-<时间戳>.state.conf`，只保留最新 5 份） |

## 项目结构

```text
openvpn-oneclick/
├── install.sh          # 唯一的一键安装入口（OpenVPN 服务端 + 面板）
├── verify.sh           # 部署后逐条复验修复项
├── README.md           # 本说明文件
├── VERSION.txt         # 版本号，供 --check-update 读取
├── pack.sh             # 由 backend/ + frontend/ 生成 panel/*.tar.gz
├── templates/          # nginx / systemd / env / sudoers / logrotate 模板
├── tests/              # 安装脚本的契约与功能测试
├── backend/            # 面板后端源码（FastAPI）
├── frontend/           # 面板前端源码（Vue 3）+ 构建产物 frontend/ovpnmanager/
└── panel/ovpnmanager-panel.tar.gz   # 安装载荷：curl|bash 安装时下载它
```

> **开发者必读（很容易踩的坑）**
>
> - 面板源码就是本仓库的 **`backend/` + `frontend/`**，不再有第二个仓库。
> - `panel/ovpnmanager-panel.tar.gz` 是**构建产物**，由 `./pack.sh` 从 `backend/`、`frontend/ovpnmanager/` 与 `templates/` 打包生成；安装脚本下载并部署的正是这个 tarball。
> - 因此**改完面板源码后必须执行 `./pack.sh` 并把重新生成的 tarball 一起提交**，否则安装脚本仍然发的是旧面板。打包前若前端有改动，请先在 `frontend/` 下执行 `npm ci && npm run build`，因为 `pack.sh` 只接受已存在的构建产物 `frontend/ovpnmanager/index.html`。
> - `verify.sh` 用于**部署后**的逐条复验（只读，不改配置、不重启服务、不吊销证书）：`sudo ./verify.sh`。
> - 面板的回归测试是 `backend/tests/test_fixes.py`（140 项断言，不依赖 pytest，需要 `httpx`，用后端虚拟环境跑：`backend/.venv/bin/python backend/tests/test_fixes.py`）。
> - `backend/tests/objective_matrix.py` 把每一条已修复的问题追溯到可执行的断言与源码证据，退出码非 0 表示有目标缺乏证据。
> - `tests/` 下是**安装脚本自身**的契约与功能测试：`check_env_contract.py`、`check_templates.py`、`check_payload.sh`、`test_functions.sh`。

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

## 验证状态与已知局限

这一节是刻意保留的，请务必读完再决定是否上生产。

| 环节 | 目前的验证方式 | 是否实机端到端验证 |
| --- | --- | --- |
| 发行版识别与包名（apt / dnf / yum） | 语法检查 + 模板 / 契约测试 | 否 |
| EasyRSA 路径发现与 PKI 初始化 | 语法检查 + 契约测试 | 否 |
| systemd 单元选择（`openvpn-server@` / `openvpn@`） | 模板契约测试 | 否 |
| 防火墙分支（firewalld / iptables）与 NAT 持久化 | 语法检查 + 契约测试 | 否 |
| 面板部署（venv、pip、nginx、sudoers 包装脚本） | 语法检查 + 单元测试 + 模板契约测试 | 否 |
| 面板自身逻辑（HTTP 层、management 客户端、CRL/CCD 权限等） | `backend/tests/test_fixes.py` 单元测试 | 否（测试跑在开发机上） |
| "修复后"的性能数字 | 本地构建与代码审查 | 否 |

**本脚本从未在真实 Linux 服务器上执行过完整流程**：开发过程中没有可用的 Linux 主机，也没有 Docker 与 WSL。因此上面这些环节目前**只由语法检查（`bash -n`）、单元测试与模板契约测试覆盖，缺少端到端验证**。

建议的做法：

1. 先在一台**可回滚**（快照 / 可重装）的测试机上跑一遍 `sudo bash install.sh --auto`；
2. 用客户端连一次 VPN，确认能出网、能打开 `http://<服务器>/ovpnmanager/`；
3. 跑一次 `sudo ./verify.sh` 逐条复验修复项；
4. 再跑一次 `sudo bash install.sh --check-update` / `--update`，确认升级不会动 PKI、`server.conf`、`SECRET_KEY` 与管理员口令；
5. 以上都通过后，才用于生产环境。

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

VPN 客户端网段需要在重新安装 / 重新生成时指定；掩码**有意固定为 /24**，不提供其它掩码。

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
