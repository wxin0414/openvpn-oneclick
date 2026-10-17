# 更新日志

本文件记录 openvpn-oneclick 面向使用者的变更：修复、行为变更与安全相关调整。版本号以 `VERSION.txt` 为准，条目按时间倒序排列，分类沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的写法。

关于版本号：仓库历史中 `VERSION.txt` 由 `1.2.2` 直接跳到 `1.2.4`，没有 1.2.3 的独立提交，因此这里没有 `[1.2.3]` 条目。1.2.2 与 1.2.3 都因下列安装缺陷无法在 Debian 12 上完成安装，修复集中在 1.2.4。

## [1.2.4] — 2026-10-17

在真实的 Debian 12 服务器上跑通完整安装与 `verify.sh` 之后，修掉了一批只在真实环境才会暴露的问题，其中前两个会让已发布的旧版本完全无法安装。

### 修复 — 安装流程

- **EasyRSA 3.1.0 的 CN 冲突**：`EASYRSA_REQ_CN` 被 `export` 后，`build-server-full` 会直接报 `does not support setting an external commonName`（3.0.x 容忍这种写法）。改为只给 `build-ca` 单独带上该变量。
- **PKI 分步补齐**：CA、服务端证书、CRL 各自独立判断，任一步缺失就补上。中断后的重跑不再被"PKI 已存在"整段跳过、随后又在签发客户端时报缺文件。
- **重跑必崩**：`report_existing_install` 的最后一条语句是 `[ -n ... ] && printf`，没有 tls 密钥时返回 1，而这是纯信息函数——在 `set -e` 下直接中断安装，导致"任何已装 OpenVPN 的机器重跑"（含 `--adopt` / `--force`）全部失败。改为显式 `return 0`，并扫掉同类的隐式退出码问题（`inject_panel_directives` 对"无需改动"返回 1）。
- **`--adopt` 在只有 PKI、没有 `server.conf` 的机器上什么都没写**：打补丁无处可打，而 `|| true` 又把失败吞了，服务必然起不来。改为文件确实存在才走打补丁路径。
- **源与目标为同一文件**：`refresh_crl` 用 `install` 拷贝时会以 `are the same file` 失败（`--adopt` 采纳既有布局时常见）。新增同文件判断，只修权限不拷贝。
- **日志污染返回值**：`info` / `ok` / `step` 输出到 stdout，被 `$(fetch_panel)` 一并捕获，于是 `tar -xzf "$asset"` 的"路径"里混进一整段日志。日志统一改到 stderr。

### 修复 — 面板与助手

- **服务状态误报 unknown**：`systemctl` 包装脚本拒绝服务名参数，而面板调用时会带上服务名。改为只接受受控的那一个服务名，传别的仍然拒绝。
- **只读查询不再走 sudo**：`systemctl show/is-active/status` 对非 root 本就允许。在某些施加了 `NoNewPrivileges` 的环境里，任何 `sudo` 都会失败；只读查询若也走 sudo，面板就会把正在运行的服务报成 unknown。只有真正需要 root 的 `start/stop/restart` 才走 sudo 白名单。
- **「服务端设置」每次保存都被拒**：`apply-config` 的 NUL 检测写成 `grep -qU $'\x00'`，而 bash 的 `$'\x00'` 会被截断成空串，等于用空模式匹配每一行——任何候选都被判成"含 NUL"。改用 `tr` 计数。
- **状态查询失败不再静默**：查询服务状态出错时改为打印命令、返回码、stdout 与 stderr；本次正是靠这条日志定位到 `sudo: The "no new privileges" flag is set`。

### 修复 — systemd 与网络环境

- **单元加固与设计冲突**：`NoNewPrivileges=true` 与 `RestrictSUIDSGID=true` 会让面板内的 `sudo` 全部失效，`ProtectSystem=full` 还会让 root 助手写不进 `/etc`。改为 `ProtectSystem=yes`（保护 `/usr`，允许写 `/etc`）并去掉前两者。
- **`verify.sh` 新增静态检查**：专门拦"shell 里能跑、服务里全挂"这类沙箱配置问题。

### 变更

- **pip 默认走国内镜像**：PyPI 的包托管在 `files.pythonhosted.org`，国内服务器上实测会 ReadTimeout 直接打断安装。现在默认使用镜像源，失败自动回退官方 PyPI，可用 `PIP_INDEX_URL` 覆盖。

## [1.2.2] — 2026-10-16

### 变更

- **迁移到 GitHub**，下载源按托管平台推导：新增 `REPO_HOST` 选择托管平台（默认 `github`）；`ASSET_BASE` 可指向自建服务器或对象存储，并兼容旧的环境变量名。
- **下载失败时的提示更可操作**：明确列出"覆盖下载源自托管""手动把面板包放到脚本同级目录""换一个允许公开托管此类内容的平台"三种做法，而不是只让人检查网络。

## [1.2.1] — 2026-10-16

修补 1.2.0「服务端设置」落地通道上的四处缺陷，其中三个属于"看起来保存成功、实际能力悄悄退化"。

### 修复

- **客户端的 `sndbuf` / `rcvbuf` 推送在第一次保存后丢失**：安装脚本的托管块里有 `push "sndbuf/rcvbuf"`，而面板只渲染服务端标量；面板整块重写托管块，于是保存一次之后客户端再也收不到缓冲调优。现在渲染时同时输出服务端标量与两条 push。
- **助手把托管块里的注释当成指令**：安装脚本在托管块里写了几行说明，指令提取器把注释行的首个 token（`#`）当指令名，不在白名单，导致每台新装机第一次保存必被拒。改为解析时跳过注释行。
- **`verify.sh` 第 10 节可能真的应用一份遗留候选并重启服务**：一个自称只读的自检脚本不应改配置。助手在成功应用后删除候选（候选本就是一次性请求），`verify.sh` 检测到候选存在时跳过实机调用、只做静态检查并说明原因。同时修掉该节里候选变量未初始化、会触发 `set -u` 的问题。

### 安全

- **助手只检查"新增"，不检查"删除"**：单向的 `comm -13` 查不出"偷偷把 `crl-verify` 整行删掉"这种降级——删掉就等于关掉证书吊销。现在助手再做一次反向比较，并要求每一个消失的行都能在候选里找到对应的退役注释 `# [ovpn-oneclick retired] <原行>`。

## [1.2.0] — 2026-10-16

此前改端口、协议、DNS、网段都必须重跑 `install.sh` 带标志；从这一版起，所有可调项都在面板里改。

### 新增

- **「服务端设置」页面**：编辑 `server.conf` 的托管块——端口、协议、推送 DNS、全局代理或分流与分流路由、IPv6 黑洞、`mssfix`、`sndbuf` / `rcvbuf`、`keepalive`、`client-to-client`、`max-clients`、`duplicate-cn`、VPN 网段与 `topology`。对应接口 `GET` / `PATCH /api/settings/server` 与 `POST /api/settings/reexport-all`，均要求超级管理员。
- **受控的特权助手**：`/usr/local/lib/ovpnmanager/apply-config` + 校验函数库 `apply-validate.sh` + root 所有的 `/etc/ovpn-oneclick/apply.conf`，sudoers 里只放行这一条**不接受任何参数**的命令。助手负责校验 → 备份 → 原子替换 `server.conf` → 按需重建 NAT 与 nginx 放行网段 → 重启 → 轮询 `is-active`，失败整体回滚。
- **`server.conf` 托管块**：固定部分（证书、`management`、`status-version`、`crl-verify`、`client-config-dir`、日志）与托管块分离，面板只写标记之间的内容。从别的项目迁移来、没有标记的配置也能用：首次保存时插入托管块、把冲突的旧行注释成 `# [ovpn-oneclick retired] <原行>`、其余行逐字节保留，替换前先备份。
- **改端口/协议后的「重新导出全部客户端」**：页面先弹确认说明旧 `.ovpn` 会失效，保存后提供一次性重导按钮，逐个客户端捕获异常并回报成功与失败清单。
- **改网段的连带处理**：助手先删除旧网段的 MASQUERADE 规则、按新网段重建 NAT 脚本，再更新 nginx 站点配置里的第一条 `allow` 并跑 `nginx -t` 校验。
- **`verify.sh` 第 10 节**：以面板用户身份无密码调用一次助手做无副作用冒烟（此时无候选文件，助手会拒绝并返回 `RESULT=failed`），同时检查助手属主/权限、`apply.conf`、sudoers 条目与面板服务的沙箱设置。`verify.sh` 也改为从 `/etc/ovpn-oneclick/state.conf` 读取服务名与配置路径。
- **`block_ipv6` 按 OpenVPN 手册输出三行**：两条 push（`ifconfig-ipv6`、`redirect-gateway ipv6`）加服务端 `block-ipv6`。只写服务端指令挡不住双栈客户端的 IPv6 泄漏。

### 修复

- **跨组件契约检查发现面板端 push 白名单漏了 `ifconfig-ipv6`**：面板生成 IPv6 黑洞配置时会被助手拒绝。两边白名单已对齐。

## [1.1.0] — 2026-10-16

### 变更

- **合并为单一项目**：一个仓库同时包含 OpenVPN 服务端一键安装脚本与 Web 管理面板源码（`backend/` 与 `frontend/`），安装载荷 `panel/ovpnmanager-panel.tar.gz` 由本仓库的 `pack.sh` 构建，安装与升级不再依赖任何第三方项目。

## 开发与测试

以下命令在仓库根目录执行，供贡献者在提交前自查。

**安装脚本与助手**

```bash
bash -n install.sh                      # 语法检查
bash tests/test_functions.sh            # 安装脚本纯逻辑（掩码换算、客户端配置生成、分流路由、客户端列表）
bash tests/test_apply_config.sh         # 特权助手的校验规则（不需要 root）
python3 tests/check_templates.py        # 每个 render 调用是否覆盖模板里的全部占位符
python3 tests/check_env_contract.py     # 分发包里的 env.template 与面板 Settings 的键是否双向对应
bash tests/check_payload.sh             # 分发包内容是否干净完整
```

**跨组件契约与面板**

```bash
python3 tests/check_apply_contract.py   # 面板与助手两边的托管块标记、指令集合、push 前缀、退役前缀逐字一致
python3 tests/test_block_parity.py      # install.sh 写入托管块的每种指令形态，面板都能渲染
python3 tests/smoke_app.py              # 整个 FastAPI 应用可导入、设置路由已注册且未被遮蔽
backend/.venv/bin/python backend/tests/test_fixes.py        # 面板回归测试（不依赖 pytest，需要 httpx）
backend/.venv/bin/python backend/tests/objective_matrix.py  # 每条已修复问题追溯到可执行断言与源码证据
```

**部署后**

```bash
sudo ./verify.sh
```

逐项复验服务、面板就绪探针、nginx、management 可达性、CRL 权限、WAL 与索引、前端产物、特权助手通道等；除第 10 节在无待应用候选时会真实调用一次助手外，其余检查只读。

**打包安装载荷**

`panel/ovpnmanager-panel.tar.gz` 是构建产物，由 `./pack.sh` 从 `backend/`、`frontend/ovpnmanager/` 与 `templates/` 打包生成，安装脚本下载并部署的正是这个 tarball。改完面板源码后：

```bash
cd frontend && npm ci && npm run build && cd ..
./pack.sh
```

`pack.sh` 只接受已存在的构建产物 `frontend/ovpnmanager/index.html`，因此前端有改动时必须先构建。重新生成的 tarball 需要和源码一起提交，否则安装的仍是旧面板。
