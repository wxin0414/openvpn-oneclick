# openvpn-oneclick

一条命令在 Debian/Ubuntu 或 RHEL 系服务器上装好 **OpenVPN 服务端 + Web 管理面板**。面板源码（FastAPI + Vue 3）就在本仓库内，安装与升级不依赖任何第三方项目。

> **免责声明**：本项目仅面向**企业内部网络**的自建远程接入，不提供任何网络接入服务，
> 也不含规避监管的功能。使用者须自行遵守所在地法律法规，因使用或滥用产生的一切后果
> 由使用者承担，作者与贡献者不承担责任。完整条款见 [DISCLAIMER.md](DISCLAIMER.md)。

```bash
wget -O install.sh https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh
sudo bash install.sh --auto
```

装完后脚本会打印面板地址与一次性初始口令，并把首个客户端的 `.ovpn` 放到服务器上。之后的日常操作——加客户端、吊销证书、改端口、改 DNS、改网段——都在面板里完成，不必再重跑脚本。

## 特性

**安装部署**

- 一条命令装完 OpenVPN 服务端与管理面板；Debian/Ubuntu 走 `apt`，RHEL/CentOS/Rocky/AlmaLinux/Fedora 走 `dnf` / `yum`。
- 自动构建 PKI：EasyRSA 生成 CA、服务端证书与 CRL，`openvpn --genkey` 生成 tls-crypt 密钥，`openssl` 生成 DH 参数。
- PKI 按文件分步补齐：上次装到一半失败，重跑会接着装，而不是被"PKI 已存在"整段跳过。
- 自动开启 `net.ipv4.ip_forward` 并配置 NAT：firewalld 活动时用 masquerade，否则写 iptables 脚本 + `ovpn-oneclick-nat.service` 持久化。
- 面板由 nginx 同源发布（`/ovpnmanager/` 静态页 + `/api/` 反向代理），对外端口默认 **TCP 5698**，面板进程本身只监听 `127.0.0.1`。
- 检测到机器上已有别人装的 OpenVPN 时默认**拒绝继续**，要求显式选择 `--adopt`（只补面板需要的指令）或 `--force`（备份后重写）。

**面板管理**

- 客户端的增删改查，证书签发、吊销、导出 `.ovpn`。
- 在线列表与强制下线（走 OpenVPN management 接口）。
- 固定 IP 与按客户端路由（生成 CCD 文件）。
- OpenVPN 服务的启动 / 停止 / 重启与状态查看。
- 审计日志（条件筛选 + 分页）与仪表盘概览。
- 令牌登录、登录限速、自助修改口令。

**服务端设置**

- 「服务端设置」页面可改端口、协议、推送 DNS、全局代理或分流及分流路由、IPv6 黑洞、`mssfix`、`sndbuf` / `rcvbuf`、`keepalive`、`client-to-client`、`max-clients`、`duplicate-cn`、VPN 网段与 `topology`。
- 保存不是直接写文件：候选配置交给 root 特权助手，校验 → 备份 → 原子替换 → 按需重建 NAT 与 nginx 放行网段 → 重启 → 起不来就整体回滚。
- 面板始终以非 root 用户运行，只被 sudo 授权固定的几条 `systemctl` 与一条不接受参数的助手命令。

**客户端**

- 导出的 `.ovpn` 内嵌客户端私钥，权限 `0600`。
- 吊销只刷新 CRL 并断开该客户端的会话，不重启服务、不影响其他在线用户。

**运维**

- `install.sh` 兼作管理入口：`--addclient` / `--revokeclient` / `--listclients` / `--exportclient` / `--check-update` / `--update` / `--uninstall`。
- 升级只替换面板代码与模板，不动 PKI、`server.conf`、`SECRET_KEY` 与管理员口令；升级前自动备份数据库与 `.env`。
- `verify.sh` 部署后逐项自检；日志由 logrotate 按天轮转。

## 系统要求

| 项目 | 要求 |
| --- | --- |
| 发行版 | Debian、Ubuntu（`apt-get`）；RHEL、CentOS、Rocky Linux、AlmaLinux、Fedora（`dnf` 或 `yum`，会尝试安装 `epel-release`） |
| 初始化系统 | systemd（安装脚本会检查 `systemctl`） |
| 权限 | root，用 `sudo bash install.sh` 运行 |
| 网络 | 需出网：发行版软件包源、本仓库的安装载荷、可选 Python 包源 |
| 地址 | 一个公网 IPv4 或域名；`--auto` 时自动探测，也可用 `--serveraddr` 指定 |
| 端口 | OpenVPN 默认 **UDP 1194**（可改）；面板经 nginx 走 **TCP 5698**，面板进程只监听 `127.0.0.1:8000` |

云服务器还需在安全组/防火墙里放行 OpenVPN 端口与面板的 TCP 5698。

## Docker 部署

```bash
git clone https://github.com/wxin0414/openvpn-oneclick.git && cd openvpn-oneclick
docker compose up -d --build
```

- 面板：`http://<host>:5698/ovpnmanager/`　OpenVPN：`udp/<host>:1194`
- 首次启动会在容器内初始化 PKI 并生成 `server.conf`（约 1–2 分钟），数据落在三个命名卷里：
  `ovpn-certs`（`/etc/openvpn`）、`ovpn-data`（面板数据库）、`ovpn-backups`。
- 必须给 `NET_ADMIN` 与 `/dev/net/tun`（compose 里已配）——否则 tun 设备建不起来、NAT 写不进去。

**容器内外同构，但有一条关键差异**：容器里没有 systemd，所以"改配置/重启服务"走的是
**sudo 通道**而不是宿主机上的 socket 激活。为此镜像内复刻了同一套权限模型：面板仍以
**非 root 用户 `ovpnpanel`** 运行，只通过 `/etc/sudoers.d/ovpnmanager` 里那几条固定规则调用
同一批助手脚本（且助手不接受参数）。**面板代码零改动。**

不适用的部分：面板里的「在线升级」与「服务重启」依赖 systemd，容器内会失败；
升级容器请用 `docker compose pull && up -d`。

> 说明：镜像构建脚本已随仓库提供，但尚未在真机上完成一次完整 `docker compose up` 验证；
> 首次使用请留意日志（`docker compose logs -f`）。

## 快速开始

全自动安装，全部使用默认值：

```bash
sudo bash install.sh --auto
```

交互式安装会依次询问公网地址、协议、端口、首个客户端名称、客户端 DNS 与是否安装面板：

```bash
sudo bash install.sh
```

两种方式都可以先带参数指定一部分选项，例如 `sudo bash install.sh --auto --proto tcp --port 443 --clientname myphone`。安装结束时打印的面板口令只出现一次，请立即保存。

完整参数（`bash install.sh --help` 的原始输出）：

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

## 安装脚本做了什么

1. **探测系统并安装依赖**：openvpn、easy-rsa、openssl、nginx、python3、iptables、rsync、logrotate、curl、ca-certificates（Debian 系另需 `python3-venv`）。
2. **构建 PKI**：初始化 EasyRSA（RSA 2048），生成 CA、服务端证书、初始 CRL、tls-crypt 密钥与 2048 位 DH 参数；已存在的文件一律复用。
3. **写 `server.conf`**：固定部分（证书、`management`、`status-version 3`、`client-config-dir`、`crl-verify`、日志）加一段面板托管的配置块。默认 `data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305`、`auth SHA256`；`fast-io` 与 `explicit-exit-notify` 只在 UDP 下写入。
4. **转发与 NAT**：写 `/etc/sysctl.d/99-ovpn-oneclick.conf` 开启 `ip_forward`，按 firewalld 或 iptables 配置 masquerade 并持久化；分流模式跳过 NAT。
5. **启动服务并签发首个客户端**：写出 `<客户端目录>/<名称>.ovpn`。
6. **部署面板**：创建非 root 系统用户、Python 虚拟环境、nginx 站点、systemd 单元、日志轮转，以及 `systemctl` 包装脚本与 `apply-config` 特权助手各自的 sudoers 条目。
7. **生成随机密钥**：`SECRET_KEY` 与管理员口令随机生成，口令只打印一次；已有 `.env` 时原样保留，只补齐新版本新增的键。

面板与本机 openvpn 服务之间的所有特权动作都收敛在两条通道上：查状态/起停服务走包装脚本，改服务端配置走 `apply-config`。

## 装完之后

**面板地址**是 `http://<服务器地址>:5698/ovpnmanager/`（访问根路径会 302 跳到该地址）。用户名 `admin`，初始口令就是安装结束时打印的那一行——它由安装脚本随机生成，写进 `/opt/ovpnmanager/backend/.env` 的 `DEFAULT_ADMIN_PASSWORD`。面板只在管理员账号缺失时才按该值播种，因此这个值同时是"重置口令"的依据。

**默认只有 VPN 网段能打开面板。** nginx 站点配置里是：

```text
allow 10.8.44.0/24;
allow 127.0.0.1;
deny all;
```

也就是说你必须先以客户端身份连上 VPN 才能访问面板，从公网直接访问会得到 403。这是刻意的默认值，不是装坏了。

需要从其他来源访问时，在 nginx 配置（`/etc/nginx/conf.d/ovpnmanager.conf`，若系统有 `sites-available` 则为 `/etc/nginx/sites-available/ovpnmanager.conf`）里加一条 `allow`，然后重载：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`/api/health/diagnostics` 这个诊断接口在 nginx 里被单独限制为仅 `127.0.0.1` 可访问，放开面板来源网段也不会把它暴露出去。

**客户端配置**在 `/etc/openvpn/server/client/<名称>.ovpn`（RHEL 系没有 `openvpn-server@` 单元时为 `/etc/openvpn/client/`）。文件内含客户端私钥、权限 `0600`：导出后应尽快取走并从服务器删除。

## 在面板里改服务端设置

入口是 `http://<服务器地址>:5698/ovpnmanager/settings`，对应接口 `GET` / `PATCH /api/settings/server` 与 `POST /api/settings/reexport-all`，均要求超级管理员。

可改项（写入 `server.conf` 托管块）：

| 分组 | 项目 | 写入的指令 |
| --- | --- | --- |
| 基本 | 监听端口 1-65535 | `port` |
| 基本 | 协议 `udp` / `tcp` | `proto`；UDP 时另生成 `explicit-exit-notify 1`，改回 TCP 时这条不再生成（注：`fast-io` 在 OpenVPN 2.5+ 已是空操作，不再写入） |
| 隧道与路由 | 全局代理 / 分流 | 全局：`push "redirect-gateway def1 bypass-dhcp"`；分流：按列表逐条 `push "route <网段> <掩码>"` |
| 隧道与路由 | 分流路由（CIDR） | 同上，仅分流模式生效；全局代理模式下提交的路由会被忽略并回显说明 |
| 隧道与路由 | 屏蔽 IPv6 | `push "ifconfig-ipv6 ..."`、`push "redirect-gateway ipv6"`、`block-ipv6` |
| DNS | 推送 DNS（最多 2 个，IPv4 / IPv6 均可） | `push "dhcp-option DNS <地址>"` |
| DNS | 阻断隧道外 DNS（Windows 防泄漏） | `push "block-outside-dns"`：Windows 客户端默认会绕过隧道用本地网卡的 DNS，开启后把这些查询也拉进隧道；其他系统忽略 |
| 性能 | `mssfix`（0-1500） | `mssfix` |
| 性能 | `sndbuf` / `rcvbuf`（0-10485760） | 服务端两条标量，并**同时推给客户端**两条 `push` |
| 性能 | `keepalive`（两个整数，如 `10 120`） | `keepalive` |
| 客户端 | 客户端互访 | `client-to-client` |
| 客户端 | 最大连接数（关闭则不下发该指令） | `max-clients` |
| 客户端 | 允许同名 CN | `duplicate-cn` |
| 网段 | VPN 网段 / 子网掩码 | `server <网段> <掩码>` |
| 网段 | topology | `topology subnet` / `net30` / `p2p` |
| 加密 | 数据加密套件 `data-ciphers` | `data-ciphers`（冒号分隔，仅接受 AES-256-GCM / AES-128-GCM / CHACHA20-POLY1305 及其组合） |
| 加密 | `auth` 摘要算法 | `auth`（AEAD 套件下不参与数据面，保留以兼容旧客户端） |
| 权限 | 运行用户 / 组 | `user` / `group`（改错会让服务起不来，保存前有二次确认，助手会自动回滚） |
| 日志 | `log-append` / `status` | 两个绝对路径，**必须是 `/var/log/openvpn/` 下**：OpenVPN 以降权用户运行，只有该目录被授权写入 || 会话 | `reneg-sec` | TLS 重协商间隔（秒，0 = 禁用）。长连接更稳，但重协商越少安全性略降 |
| 会话 | `inactive` | 空闲超过该秒数自动断开（0 = 禁用），配合"最大连接数"回收名额 |
| 安全 | `tls-version-min` | TLS 最低版本 1.0/1.1/1.2/1.3。禁掉旧版 TLS，客户端 OpenSSL 太老会连不上 |
| 日志 | `verb` / `mute` | 日志详细度 0-11、同类消息抑制条数。这五项都是**可选**：不设置就不写进配置，用 OpenVPN 默认 |

保存时由接口侧做最终校验，不通过返回 400 并给出具体中文原因：

- 端口 1-65535；协议只接受 `udp` / `tcp`（不开放 `udp6` / `tcp6`）；`topology` 只接受三种取值。
- DNS 最多 2 个且必须是合法 IPv4 / IPv6 地址；分流路由必须是合法 CIDR。
- 网段必须是**网段地址**：`10.9.0.7` + `255.255.0.0` 会被明确拒绝而不是悄悄规范化；掩码必须连续且前缀长度 ≥ 8；网段与掩码必须匹配。
- `PATCH` 只带发生变化的字段，未提交的项沿用当前值；提交只读项或任何未知键一律 400。

页面底部把这些指令列为**只读**，只展示不提供编辑：`management` 与 `status-version`（面板靠它们读在线状态）、`crl-verify`（吊销是否生效全靠它）、`client-config-dir`（固定 IP 与按客户端路由）、tls 模式（`tls-crypt` / `tls-auth`，与密钥和既有客户端绑定）、`cipher`（旧版对称加密，与 `data-ciphers` 冲突）。`data-ciphers` 与 `auth` 是**可编辑的**（见上表），改错会让既有客户端握手失败，保存前会提示需要重新导出客户端配置。

**改端口或协议会让已发出的客户端配置全部失效。** 页面在保存前会弹一次确认说明这一点；保存后结果卡片会标出「客户端配置受影响」并给出「重新导出全部客户端」按钮。该按钮把所有未吊销、未被禁用的客户端一次性重新导出（同步执行，只读 PKI 与 tls 密钥，不跑 easyrsa / openssl），逐个捕获异常并回报成功与失败清单——但**导出的文件仍需要你自己重新分发**。

**改 VPN 网段、掩码或 topology 会连带改动周边。** 助手会先删掉旧网段的 MASQUERADE 规则、按新网段重建 NAT 脚本，再把 nginx 站点配置里的第一条 `allow` 改成新网段并跑 `nginx -t` 校验，最后重启服务；已有客户端的固定 IP 需要你同步调整。无论改哪一项，助手在成功路径的最后都会重启一次 OpenVPN 并轮询 `is-active`（默认最多等 20 秒），因此每次保存都会让在线客户端短暂断开一次。

**安全模型：面板从不直接写系统文件。** 面板以非 root 运行，`server.conf`、NAT 脚本、nginx 站点配置都归 root。流程是：面板把"想改成的样子"渲染成一份完整候选配置写进自己的数据目录（`/opt/ovpnmanager/backend/data/server.conf.candidate`，`0600`）→ 通过 **systemd socket 激活**把候选交给 root 助手（`/run/ovpn-oneclick/apply.sock` → `apply@.service`，以 root 运行；**不带任何参数**，路径全部来自 root 所有的 `/etc/ovpn-oneclick/apply.conf`）。注意：面板进程带 `NoNewPrivileges=1`，**任何 `sudo` 都会被内核拒绝**，所以所有提权动作（配置写入、服务启停、备份、恢复、升级）都走这条路，`sudo` 只是没有 socket 时的回退→ 助手以 root 校验、备份、原子替换、按需重建 NAT / nginx、重启并等待就绪，失败整体回滚 → 面板解析助手输出的 `RESULT=` / `BACKUP=` / `RESTARTED=` / `ROLLED_BACK=` / `NETWORK_CHANGED=` / `REASON=` 回报页面。

助手的校验有四条，缺一不可：候选文件必须存在且是普通文件、非空、不超过 128 KB、不含 NUL、属主是面板用户且不是组/其他用户可写；托管块内只能出现白名单指令（`port proto server topology data-ciphers auth user group log-append status mssfix sndbuf rcvbuf keepalive client-to-client max-clients duplicate-cn block-ipv6 push explicit-exit-notify`），`push` 的载荷还要逐个匹配允许的形态；托管块之外不得引入当前配置里没有的指令；候选也不能让既有指令凭空消失——只允许把它们注释成 `# [ovpn-oneclick retired] <原行>`。

因此 `server.conf` 里那段托管块以外的内容不受面板影响，反过来说：**手工编辑托管块里的行没有意义**，每次保存都会按表单内容整段重新生成。若 `server.conf` 不是本安装器写的（例如 `--adopt` 采纳来的），它没有托管块；第一次保存时面板会插入托管块、把冲突的旧指令注释退役并先备份，页面会提前给出提醒。

## 客户端管理

面板里能做的事：新增客户端并签发证书、编辑固定 IP 与按客户端路由、导出 `.ovpn`、吊销证书、强制下线、删除记录。证书签发与吊销以后台任务执行，前端轮询进度，一次耗时的 EasyRSA 运行不会把浏览器请求拖到超时。

命令行等价入口（都要 root）：

| 命令 | 作用 |
| --- | --- |
| `sudo bash install.sh --addclient <名称>` | 签发证书并导出 `.ovpn`；同名已存在时直接重导 |
| `sudo bash install.sh --exportclient <名称>` | 重新导出已有客户端的 `.ovpn` |
| `sudo bash install.sh --listclients` | 列出客户端，已吊销的会标注 |
| `sudo bash install.sh --revokeclient <名称>` | 吊销证书并刷新 CRL，同时尝试断开该客户端会话 |
| `sudo bash install.sh --uninstall` | 卸载服务与面板（二次确认，软件包保留） |

吊销不会重启 OpenVPN：脚本只重新生成 CRL（OpenVPN 检测到文件变化后会重读它），再通过 management 接口 `kill` 掉这个客户端的会话。若 management 端口正被面板占用，它会如实告诉你未能确认断开，此时可在面板里对该客户端执行「强制下线」。

## 分享给同事（一次性链接与二维码）

「客户端管理」里每个客户端多了一个 **分享** 按钮：生成一条**一次性**下载链接，并附带二维码。

- 二维码内容是**链接**，不是配置文件。内联证书/密钥的 `.ovpn` 约 8 KB，而二维码
  字节模式的容量只有 2–3 KB，装不下；链接只有几十字符，手机扫码即可下载。
- 链接一次性、限时（默认 60 分钟）。数据库里**只存令牌的 sha256**，不存明文。
- 下载地址是公开的（`/share/<token>`，nginx 里单独放行、不继承面板的 allow/deny）——
  同事拿到链接时还没连上 VPN，要求"先连 VPN 才能下载"是死循环。安全性由令牌保证
  （一次性、限时、只存哈希），不依赖来源 IP。
- 用过一次即失效（再访问返回 `410 Gone`）。要再发就重新生成。

> ⚠️ 这条链接等同于该客户端的使用权：它包含客户端证书和私钥。发给谁就等于把
> 这个客户端的访问权交给谁。客户端丢失或换人时，请在面板里吊销并重新签发。

## 面板自身设置

入口 `http://<服务器地址>:5698/ovpnmanager/panel`（菜单「面板设置」），对应 `GET` / `PATCH /api/panel`，仅超级管理员。

| 项目 | 说明 |
| --- | --- |
| 监听端口 | 改完立即重建 nginx 站点并 reload，**旧端口当即失效** |
| 绑定域名 | nginx `server_name`，默认 `_` |
| 放行来源 | `public`（允许所有来源）或指定 CIDR 列表。面板一旦公网可达，请务必同时开启 TLS |
| TLS | `off` / `self-signed` / `existing`；`self-signed` 会现场生成自签证书并切到 HTTPS |
| 管理员账号口令 | 改用户名与密码，立即生效 |
| 会话有效期 | 5–10080 分钟，写入 `.env` 后重启面板 |

与「服务端设置」走同一条 socket 助手路径：助手先写 nginx 配置、跑 `nginx -t`，通过才 reload，失败整体回滚。改端口或 TLS 会让当前页面地址失效，保存后按提示用新地址访问。

## 网络转发（ip_forward 与 NAT 出口网卡）

入口「面板设置 → 网络转发」，对应 `GET` / `POST /api/panel/network`。

- **ip_forward 开关。** 关闭等于客户端不能出网，但 OpenVPN 服务本身**不会报错**（状态仍是 active）——这是最容易误判为"服务正常"的故障，所以界面上关闭前会二次确认。
- **NAT 出口网卡。** 下拉里只列真实存在的接口；助手还会交叉验证该接口**确有默认路由**，否则拒绝（改错了客户端立刻失去出网，而重启服务发现不了）。
- 规则同时写进 `ovpn-oneclick-nat.service`，开机自恢复。

## 流量历史

面板每分钟把管理接口里的累计字节数落一次库（`traffic_samples` 表），Dashboard 用折线图 + Top 客户端表格展示。

- **数据来源**：`management` 接口的 `bytes_received` / `bytes_sent`。它们是**会话累计值**，
  客户端重连后会归零。所以库里存累计值、聚合时算增量，并对"归零"做专门处理
  （`cur < prev` 视为从 0 重新开始，而不是算出负流量）。
- **采样间隔** `TRAFFIC_SAMPLE_SECONDS`（默认 60 秒，最小 15）；
  **保留期** `TRAFFIC_RETENTION_DAYS`（默认 30 天）。都可用环境变量覆盖。
- 只有**在线**客户端才有数据；下线后停止累加，历史保留。
- 图表是**手写 SVG 折线**，没有引入图表库——只有一条上下行曲线，
  引入 ECharts 会让前端包体大一个数量级。
- 历史接口：`GET /api/traffic/overview?hours=24`（可选 1–720 小时）。

## 证书到期

Dashboard 新增「证书到期」卡片：列出未来 30 天内会到期的客户端证书，并用告警条提示；
已过期未吊销的会标红并显示过期天数。

- 接口：`GET /api/dashboard/certificate-expiry?days=30`（1–3650 天）。
- Dashboard 的 `alerts.count` 现在等于"30 天内到期/已过期且未吊销"的证书数——
  此前这个字段是**写死的 0**，等于把最重要的运维提醒藏了起来。
- 客户端证书默认 **825 天**。到期后客户端无法连接，需要在「客户端管理」里吊销并重新签发。

统计口径（`total = valid + revoked + expired`）：
`valid` 是"状态有效**且**未过期"，`expired` 是"未吊销**且**已过期"，两者不重叠。
之前 `valid` 只判状态、`expired` 不排除已吊销，导致四个分项加起来会超过 `total`。

## 配置历史与回滚

「服务端设置」页面底部新增 **配置历史** 卡片：列出每次保存前留下的
`server.conf.bak.YYYYMMDD-HHMMSS`，可以查看与当前配置的差异，或直接用某份备份回滚。

- 接口：`GET /api/settings/history`、`GET /api/settings/history/{name}`（含 unified diff）、
  `POST /api/settings/history/{name}/restore?confirm=RESTORE`。
- **回滚复用同一条落地通道**：把备份内容原样写成候选文件，再交给 `ACTION=server-conf`
  助手做备份/原子替换/重启/就绪检查/失败自动还原。不另写特权脚本，风险面不增加。
- 回滚前会先备份当前配置，所以"回滚错了"也能再回滚回来。
- 备份必须先通过候选校验才会被接受。早期版本里已被移除的指令会被拒绝——宁可拒绝，
  也不要把一份"当年能跑、现在可能起不来"的配置写回去。
- 文件名严格匹配 `server.conf.bak.<8位日期>-<6位时间>`，且必须是 `/etc/openvpn` 下的普通文件
  （拒绝符号链接与路径穿越）。

## 一键诊断（面板内）

「面板设置」页新增 **一键诊断** 卡片：点一下就把 11 项只读检查跑一遍，每项都带处置建议。
出问题不用登 SSH。

检查项：OpenVPN / 面板 / nginx 服务状态、特权助手通道（`apply.socket`）、IP 转发、
托管配置块、管理接口、OpenVPN 端口是否在监听、`ta.key` 是否面板可读、证书到期、磁盘空间。

- 接口：`GET /api/health/selfcheck`（需登录）。
- 与 `GET /api/health/diagnostics` 的区别：那个被 nginx 限制成只能本机访问（留给监控），
  浏览器根本调不到；这个是给面板用户点的。
- **每项独立容错**：某一项探不到（无 systemd、无权限）只把它标成 `unknown`，
  绝不让整个自检 500 —— 最需要诊断的时候它不能先挂。
- 只读：不重启服务、不改配置。

## 备份与恢复

**一键备份。** 面板「面板设置 → 备份」，或 `POST /api/panel/backup`。备份四样真正无法重建的东西：PKI（`/etc/openvpn/easy-rsa`）、`server.conf`、`backend/.env`（`SECRET_KEY` 与口令）、`backend/data/app.db`。产物在 `/var/backups/ovpn-oneclick/manual-<时间戳>.tar.gz`，权限 `0600`（内含 CA 私钥与 `SECRET_KEY`，**不要外传**），只保留最近 10 份。备份列表的索引在 `/etc/ovpn-oneclick/backup-index.jsonl`（`0644`，只含路径/大小/时间，不含密钥内容）。

**恢复。** 在备份列表点「恢复」，需要**手打 `RESTORE`**。助手的动作顺序：再备份一次当前状态 → 把现有四项整体 `mv` 到临时目录（可原子还原）→ 解包 → 校验 `server.conf` 存在 → 重启 OpenVPN 并要求连续 4 秒 `active`；任何一步失败都整体还原并重启。恢复完成后面板会延迟 3 秒自动重启（数据库已换掉，不重启读的还是旧库）。

## 在线升级（面板内）

面板「当前状态 → 检查更新 / 升级」。

- **检查更新**只读：拉本仓库 `VERSION.txt` 与已安装版本比对，按数字逐段比较。
- **升级**需要手打 `UPGRADE`，且是**异步**的：接口立刻返回，助手起一个脱离会话的后台进程执行 `install.sh --update`，进度写 `/etc/ovpn-oneclick/upgrade.state`，前端轮询。升级过程中面板会重启，所以轮询期间的请求失败属正常。
- 优先使用安装时留下的本地安装器副本 `/usr/local/lib/ovpnmanager/install.sh`——实测服务器到 `raw.githubusercontent.com` 并不稳定，只有副本缺失时才回退下载。
- 升级**不会自动降级**；失败时配置与证书都在备份里，按上一节恢复即可。

## 性能调优（安装时自动完成）

安装脚本写入 `/etc/sysctl.d/99-ovpn-oneclick.conf` 并立即生效：

- `net.ipv4.ip_forward=1`
- **socket 缓冲抬到 4 MiB**（`rmem_max` / `wmem_max`）。系统默认只有 208 KiB，会**静默截断** `server.conf` 里的 `sndbuf` / `rcvbuf`（默认 384 KiB），并在高延迟客户端上直接压住吞吐。
- 内核提供 `tcp_bbr` 时启用 **BBR + fq**（对高 RTT、有丢包的链路明显优于 cubic）；不提供则保持原值并打印提示。

参考数据：单核 AES-256-GCM 约 1.68 GB/s（≈13 Gbps），**加密从来不是瓶颈**，真正的上限是链路带宽。可用 `bash install.sh --perf-report` 之外的第三方工具实测；本仓库的实测方法与结论记录在 `docs/` 中。

## 维护

**升级。** 先看版本，再升级（都要 root 与出网）：

```bash
sudo bash install.sh --check-update
sudo bash install.sh --update
```

`--check-update` 只打印已安装版本（取自 `/etc/ovpn-oneclick/state.conf`）、当前脚本版本与仓库 `VERSION.txt` 的远端版本，不改任何东西。`--update` 会替换面板代码与模板（含特权助手），并**刻意不动**这些：

- PKI 与 tls 密钥：不初始化证书体系、不重写 `server.conf`，你在面板里改过的设置因此原样保留；
- `SECRET_KEY` 与管理员口令：`.env` 已存在时保留原值，只把新版本模板里新增的键追加到文件末尾；
- 备份：升级前把 `backend/.env` 与 `backend/data` 打包到 `/var/backups/ovpn-oneclick/panel-<时间戳>.tar.gz`，状态文件另存一份，只保留最新 5 份；
- 自更新：脚本自身下载到临时文件后先检查首行、再 `bash -n` 语法校验，通过才备份并原子替换；任何一步失败都放弃自更新且不影响面板升级。以 `curl | bash` 方式执行时跳过自更新。

**备份。** 真正需要备份的是三样：`/etc/openvpn`（PKI、`server.conf`、客户端 `.ovpn`）、`/opt/ovpnmanager/backend/.env`（`SECRET_KEY` 与口令）、`/opt/ovpnmanager/backend/data/app.db`（客户端与审计记录）。`--update` 的自动备份只覆盖后两者。

**卸载。** `sudo bash install.sh --uninstall` 会停用并删除面板、nginx 站点、NAT 单元、sudoers 条目、助手与面板目录，然后单独询问是否连 `/etc/openvpn`（全部证书与配置）一起删除。加上 `-y` 会跳过两次确认，并连 `/etc/openvpn` 与日志目录一起删除。系统软件包不会卸载，脚本会在结尾告诉你清理命令。

**日志位置。**

- OpenVPN：`/var/log/openvpn/openvpn.log`，在线状态 `/var/log/openvpn/openvpn-status.log`；`journalctl -u openvpn-server@server`（无 `openvpn-server@` 单元时为 `openvpn@server`）。
- 面板：`/opt/ovpnmanager/logs/ovpnmanager.log`；`journalctl -u ovpnmanager`。
- 服务端设置的落地记录：`/etc/ovpn-oneclick/apply.log`，最近一次结果在 `/etc/ovpn-oneclick/last-apply.conf`。
- `server.conf` 的备份：`<server.conf>.bak.<时间戳>`。

## 常见问题

**面板打不开。** 先确认你是从 VPN 网段访问的——默认只允许 `10.8.44.0/24` 与 `127.0.0.1`，公网直连会被 `deny all` 挡掉；再看云安全组是否放行了 TCP 5698。想从别的网段访问，按「装完之后」一节加 `allow` 并 reload nginx。

**装依赖很慢或 pip 超时。** 系统包走发行版源，面板依赖默认装自阿里云 PyPI 镜像，失败会自动回退官方源。想换源直接覆盖环境变量：

```bash
PIP_INDEX_URL=https://pypi.org/simple/ sudo -E bash install.sh --auto
```

**服务起不来。** 安装脚本在启动失败时会打印最后 30 行日志并退出。手工排查：`journalctl -u openvpn-server@server`（或 `openvpn@server`）、`journalctl -u ovpnmanager`；装完之后还可以跑一遍 `sudo ./verify.sh` 逐项检查服务、面板就绪探针、nginx、management 可达性、权限与特权助手通道。

**改了端口或协议之后客户端连不上。** 这是预期行为：旧 `.ovpn` 里的 `remote` 还是旧端口/协议。到「服务端设置」页面点「重新导出全部客户端」，再把新文件分发出去；同时在安全组里放行新端口。改完记得确认你登面板用的那条连接没有一起断掉。

**想用 TCP 443，仍然连不上。** 本机 443 没有被 nginx 占用（nginx 只监听 5698），所以端口冲突一般不在本机。先确认安全组放行的是与 `proto` 一致的协议——`udp` 与 `tcp` 是两条独立规则，部分网络会直接丢弃 UDP 443；TCP 443 更容易穿过只放行常见端口的网络，但 TCP over TCP 在丢包链路上会有明显的吞吐退化，只在确实需要时使用。

**吊销了证书，客户端还能连。** 检查 CRL 权限是否为 `0644`：OpenVPN 是在降权到 `nobody` 之后才重读 CRL 的，文件若是 `0600` 它读不到却也不报错，吊销就静默失效。

**忘记面板口令。** 管理员账号只在缺失时才创建，因此删掉该行再重启面板，它会按 `.env` 里的 `DEFAULT_ADMIN_PASSWORD` 重新播种：

```bash
sudo python3 -c "import sqlite3; c = sqlite3.connect('/opt/ovpnmanager/backend/data/app.db'); c.execute(\"DELETE FROM users WHERE username='admin'\"); c.commit(); c.close()"
sudo systemctl restart ovpnmanager
```

**客户端连上但没有网络。** 依次看 `net.ipv4.ip_forward` 是否为 `1`、NAT 规则是否存在（`iptables -t nat -L POSTROUTING -n`）、云安全组是否放行出网；分流模式下本就没有 NAT，需要确认推给客户端的是哪几条路由。

## 安全说明

- **PKI 归属。** CA、服务端证书与客户端私钥由 EasyRSA 生成，目录归面板用户所有（面板要能签发与吊销证书）；tls-crypt 密钥与服务端私钥在创建时即 `0600`。整套 PKI 一旦泄露等于所有客户端身份可被冒用，备份与访问控制按同等敏感度处理。
- **面板不是 root。** systemd 单元以专用系统用户运行、`UMask=0077`，并通过 `ProtectSystem=yes` 只把 `/usr` 挂成只读（不能用 `full` / `strict`，那会连 `/etc` 一起只读，root 助手就写不进 `server.conf`）。单元里也刻意不加 `NoNewPrivileges=true` / `RestrictSUIDSGID=true`：面板靠 `sudo` 调受控助手，禁止提权会让这两条 sudo 通道全部失效。
- **sudo 白名单。** `/etc/sudoers.d/ovpnmanager`（`0440`，`visudo -c` 校验）只放行两件事：固定的几条 `systemctl` 子命令（由包装脚本把 unit 强制成受控服务），以及 `/usr/local/lib/ovpnmanager/apply-config` 这一条**不接受任何参数**的命令。读状态之类的只读查询优先直连，避免把提权面放大。
- **CRL 必须是 `0644`。** 这不是随手放宽权限：OpenVPN 降权到 `nobody` 之后才重读 CRL，`0600` 会让它读不到而不报错，结果是吊销根本不生效。同理 CCD 文件按 `0644` 写入，否则固定 IP 与按客户端路由会被静默忽略。
- **`SECRET_KEY` 轮换的代价。** 它只用于签发登录令牌，换掉之后所有已登录会话立即失效、需要重新登录；不影响 PKI、客户端证书与 VPN 连接。安装脚本生成的是随机值，`/api/health/diagnostics` 会在仍使用公开默认值时给出告警。
- **登录保护。** 默认 5 分钟内 8 次失败即锁定 5 分钟，可随时在面板里自助修改口令。

## 免责声明

本项目只在你自己的服务器上安装 OpenVPN 服务端与管理面板，**不提供任何网络接入服务**，也不含规避监管的功能；它面向**企业内部网络环境**的自建远程接入与运维管理，只应用于**你拥有或已获得明确授权**的网络与设备。使用者应自行确保使用行为符合所在地法律法规与所在单位规章制度，因使用、误用或滥用本软件产生的一切后果与法律责任由使用者自行承担；作者与贡献者不承担任何责任，也不对任何第三方的使用行为负责或背书。本项目按 MIT 许可「按原样」（AS IS）提供，不附带任何明示或暗示的担保；如需对外提供商业性网络接入服务，须自行取得相应的经营资质与许可。

完整条款见 [DISCLAIMER.md](DISCLAIMER.md)。

## 已验证环境 / 已知限制

以下结论来自真实服务器上的完整安装（全新 Debian 12 KVM、OpenVPN 2.6.14）：一条命令装完，OpenVPN 服务运行中，面板与 nginx 均为 `active`；`sudo ./verify.sh` 的结果是 **33 项通过、0 失败、3 跳过**；「服务端设置」端到端跑通了一次——面板用户写候选、助手返回 `RESULT=ok`、生成 `.bak` 备份、原子替换后服务仍为 `active`、候选文件被自动清理，而在托管块外夹带 `script-security` 的候选被助手拒绝且配置一个字节都没变。

尚未覆盖的部分：

- **其他发行版。** RHEL 系的包名、`openvpn@` 单元与 EPEL 分支只做过静态检查与模板契约测试，没有实机跑过完整安装。生产使用前请先在可回滚的机器上验证。
- **IPv6 黑洞的实际链路效果。** 配置形态与 OpenVPN 手册 `--block-ipv6` 一节一致（两条 push + 服务端一条），但没有在双栈链路上验证泄漏是否真的消失；`block-ipv6` 需要 OpenVPN 2.4+，安装脚本会在低版本上提前拒绝该参数。
- **面板的规模表现。** 在线状态由后台每 30 秒同步一次，列表与审计分页查询按常规量级设计，没有做过数百客户端以上的压力测试。

还有一条行为约定需要留意：分流模式且未显式指定 `--dns1` 时不推送任何 DNS（推公共 DNS 会把内网域名也解析到公网），内网需要域名解析时请显式指定内网 DNS。

## 许可与致谢

本项目以 **MIT** 许可证发布，见 [LICENSE](LICENSE)。

面板源自 **lison** 的 `ovpnmanager`（MIT 许可）。OpenVPN 服务端安装逻辑是本仓库的独立实现，其交互风格与命令行设计参考并受启发于 [hwdsl2/openvpn-install](https://github.com/hwdsl2/openvpn-install) 与 [Nyr/openvpn-install](https://github.com/Nyr/openvpn-install)（均为 MIT 许可），在此致谢。

版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。使用前请阅读 [DISCLAIMER.md](DISCLAIMER.md)：本项目只面向企业内部网络的自建远程接入，合规责任由使用者自行承担。`docs/` 下是若干历史部署笔记（TCP 部署、客户端说明、内网双向访问、firewalld 放行），其中的网段与端口是当时环境的示例值，与本仓库的默认值不一定相同。
