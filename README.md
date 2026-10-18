# ovpn-oneclick

**中文** | [English](README.en.md)

[![CI](https://github.com/wxin0414/openvpn-oneclick/actions/workflows/ci.yml/badge.svg)](https://github.com/wxin0414/openvpn-oneclick/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wxin0414/openvpn-oneclick?display_name=tag)](https://github.com/wxin0414/openvpn-oneclick/releases)
[![License](https://img.shields.io/github/license/wxin0414/openvpn-oneclick)](LICENSE)

一条命令装好 OpenVPN 服务端 + 网页控制面板。**改配置不用登 SSH**——面板把"想改成什么样"渲染成候选文件，交给
root 特权助手校验、备份、原子替换、重启，起不来就整体回滚。

> 和"只要一个 install 脚本"的项目不同，本项目的重点在**装完之后**：证书、客户端分发、
> 配置变更、回滚、自检、只读运维账号，都在面板里完成。

---

## 特性

- **一条命令安装**：探测系统 → 装依赖 → 建 PKI → 写配置 → 配 NAT → 起服务 → 部署面板 → 自检
- **29 项服务端设置**在面板里改（端口、协议、网段、DNS、分流、加密套件、TLS 下限、日志级别……）
- **客户端全生命周期**：签发、吊销、导出、固定 IP、按客户端路由、强制下线
- **一次性链接与二维码分发**：生成限时下载链接 + 二维码，手机扫码即导入；链接一次性、库里只存哈希
- **可观测**：流量历史折线图、证书到期提醒、11 项一键自检（每项都带处置建议）
- **可回退**：配置历史 + 差异对比 + 一键回滚（回滚前自动再备份一次）
- **只读运维账号**：`viewer` 角色能看所有页面，所有写操作被全局中间件拦成 403
- **越权防护**：面板以非 root 运行，带 `NoNewPrivileges=1`，靠 systemd socket 激活拿特权

---

## 安装

Debian 12 / Ubuntu 22.04+，root 权限，放行 **1194/udp**（OpenVPN）与 **5698/tcp**（面板）。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh) --auto
```

<details>
<summary>如果服务器下载不动 GitHub（国内常见）</summary>

实测部分国内机器到 `raw.githubusercontent.com` 会卡住（大文件尤其明显）。把仓库拷到服务器再跑更稳：

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh
# 同时把 panel/ovpnmanager-panel.tar.gz 放到 install.sh 同级的 panel/ 目录（脚本会优先用本地包，不联网）
bash install.sh --auto
```

安装器会自动给 apt 加超时并测速，仅在官方源明显更慢时切换国内镜像（改前备份原文件）。

</details>

装完会打印面板地址与初始口令；口令也可从 `/opt/ovpnmanager/backend/.env` 的
`DEFAULT_ADMIN_PASSWORD` 取回（`0600`，仅 root 与面板用户可读）。

```text
面板     : http://<你的IP>:5698/ovpnmanager/
OpenVPN  : udp://<你的IP>:1194
```

> 云服务器的**安全组**要单独放行这两个端口——这和机器上的防火墙是两回事。

---

## 面板里能改什么

### 服务端设置（29 项可写 / 24 条托管指令 / 7 种 push 前缀）

| 分组 | 可改 | 约束 |
| --- | --- | --- |
| 监听 | `port`、`proto`（udp/tcp） | 改完重建 nginx 放行与 NAT，旧端口立即失效 |
| 隧道 | VPN 网段、掩码、`topology` | 改网段连带重建 NAT、改 nginx 放行，固定 IP 需同步调整 |
| 路由 | 推送 DNS（最多 2 个，IPv4/IPv6）、全局或分流 + 分流路由、`block-ipv6` | 分流路由下发为 `push "route"` |
| 加密 | `data-ciphers`、`auth`、`tls-version-min` | 改错会让既有客户端握手失败，保存前提示需重新导出 |
| 会话 | `keepalive`、`reneg-sec`、`inactive`、`max-clients`、`duplicate-cn`、`client-to-client` | — |
| 性能 | `mssfix`、`sndbuf`、`rcvbuf` | 安装时已调 sysctl 与拥塞控制 |
| 权限 | `user`、`group` | 降权后服务起不来会整体回滚 |
| 日志 | `log-append`、`status`、`verb`、`mute` | 两个路径必须在 `/var/log/openvpn/` 下 |
| DNS 防泄漏 | `push "block-outside-dns"` | 只对 Windows 客户端有意义 |

只读展示：`management`、`status-version`、`crl-verify`、`client-config-dir`、tls 模式、`cipher`。

### 其余页面

| 页面 | 能做什么 |
| --- | --- |
| 客户端管理 | 新增/编辑/吊销/删除、下载 `.ovpn`、**分享**（一次性链接 + 二维码）、按平台下载客户端 |
| 用户管理 | 新增/改口令/停用/删除账号；角色分 `admin` 与只读 `viewer`；可改自己的口令 |
| 面板设置 | 监听端口、域名、放行网段、TLS、管理员凭据、备份与恢复、在线升级、**一键诊断** |
| 服务端设置 | 上面那张表；页底还有**配置历史**（差异 + 回滚） |
| 仪表盘 | 在线数、流量折线图、**告警汇总**（服务/助手通道/磁盘/证书）、连接事件、路由表 |

---

## 日常操作

```bash
# 面板挂了也能用：命令行保留完整能力
bash install.sh --addclient alice
bash install.sh --listclients
bash install.sh --exportclient alice
bash install.sh --revokeclient alice
bash install.sh --update          # 升级（面板里也有「在线升级」）
bash install.sh --uninstall
```

**备份**：面板「面板设置 → 备份」，或 `POST /api/panel/backup`。备份 PKI、`server.conf`、
`backend/.env`、`app.db` 四样无法重建的东西，产物 `0600`，只保留最近 10 份。

---

## 安全模型

<details>
<summary>面板为什么不用 sudo（以及特权动作怎么落地）</summary>

面板进程带 `NoNewPrivileges=1`（部分云厂商强制施加，从 unit 上关不掉），**进程内任何
`sudo` 都会被内核拒绝**：

```text
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

所以所有提权动作走 **systemd socket 激活**：

```text
面板 → /run/ovpn-oneclick/apply.sock（0660，属组为面板用户）
     → apply.socket（Accept=yes，systemd 以 root 拉起 apply@.service）
     → apply-dispatch（Python，按 ACTION= 分派到固定脚本，不接受任何参数）
     → 校验 → 备份 → 原子替换 → 重启 → 等就绪 → 失败整体回滚
```

助手会拒绝：非普通文件、空文件、>128 KB、含 NUL、属主不是面板用户、组/其他可写、
托管块里出现白名单之外的指令、托管块外凭空新增指令、让既有指令静默消失（只允许注释退役）。

「起不来」的判定不看 `systemctl restart` 的退出码，而是要求服务**连续若干秒保持 active**。

</details>

<details>
<summary>凭据与权限</summary>

- PKI 归面板用户所有（它要签发/吊销）；`ta.key` 为 `root:面板用户 0640`；其余 `0600`
- `/etc/sudoers.d/ovpnmanager`（`0440`，`visudo -c` 校验）只放行固定的几条 `systemctl`
  子命令与一个**不接受参数**的助手脚本
- 一次性下载链接：库中只存令牌的 sha256，并发兑换用条件更新保证只有一次成功
- 客户端名称做白名单校验（`app/services/names.py`）：它要拼进 `pki/`、`ccd/` 路径并作为 easyrsa 参数，
  因此禁止 `/`、`\`、`..` 与 `-` 开头（实测未校验时可写到 CCD 目录之外）
- 只读账号：全局中间件拦截 `viewer` 的所有 `POST/PUT/PATCH/DELETE`（放行登录接口）
- `SECRET_KEY` 只用于签发登录令牌；换掉会让所有已登录会话失效，不影响 VPN

</details>

---

## Docker（可选）

```bash
docker compose up -d --build
```

镜像内含 OpenVPN + 面板 + nginx。容器里没有 systemd，因此特权动作走 **sudo 通道**而不是
socket 激活：面板仍以非 root 用户运行，只通过 sudoers 里几条固定规则调用同一批助手（代码零改动）。

> **镜像构建尚未在真机上验证完成**（作者的目标机未安装 Docker 守护进程）。已用
> `sudo bash docker/verify-offline.sh` 做离线验证：配置生成、指令合法性、面板解析、`nginx -t`、
> compose 字段，以及"助手安装名与 sudoers/面板期望一致"。构建与运行期行为请以实际日志为准。

---

## 明确未实现的功能

常被问起、但**没有做**的两项，面板不提供入口：

- **重建 CA**：会让所有既有客户端证书失效
- **轮换 tls-crypt 密钥**：会让所有既有 `.ovpn` 立即作废（它们内联了旧密钥）

不做按钮的理由：影响面覆盖全部客户端且不可逆，误操作代价远大于便利。替代路径是
**提前换签**（配合证书到期提醒）+ **配置历史回滚**。

---

## 已验证环境

Debian 12（kernel 6.1）、OpenVPN 2.6、KVM 单机，国内公网链路。CI 在每次推送跑安装器断言、
助手与面板的契约检查、以及后端 239 项断言。

## 致谢

- [hwdsl2/openvpn-install](https://github.com/hwdsl2/openvpn-install) —— 安装脚本结构与交互设计的参考
- [slc14211421/ovpnmanager](https://github.com/slc14211421/ovpnmanager) —— 最初的面板灵感来源（本仓面板为重写）
- [Nyr/openvpn-install](https://github.com/Nyr/openvpn-install) —— 更早的 OpenVPN 安装脚本

## 许可

见 [LICENSE](LICENSE) 与 [DISCLAIMER.md](DISCLAIMER.md)。
