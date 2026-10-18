# 全项目审计记录

逐模块核对 openvpn-oneclick。每个模块记录：核对了什么 → 结论 → 证据 → 修复。

**原则**：没真读代码或没真机验证的，不写"通过"。核对方法出错导致的假警报也记录在案，避免后人重复踩。

---

## 模块 1：安装器 `install.sh` —— ✅ 完成

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 严格模式与错误捕获 | 通过 | L26 `set -Eeuo pipefail`；L184 `trap ... ERR` |
| `-h/--help` | 通过 | L287 `-h\|--help) usage; exit 0` |
| 未知选项 | 通过 | L289 `*) die "未知选项：$1"` —— 不会静默当默认安装 |
| 带值选项校验 | 通过 | `--proto/--port/--serveraddr/--addclient/...` 全部走 `require_value` |
| 动作分发完整性 | 通过 | `addclient/revokeclient/exportclient/listclients/uninstall/check-update/update` 均有分支（`listclients` 在 L2215） |
| 卸载确认 | 通过 | L1459 需输入 `yes`；L1487 删 `/etc/openvpn` 前**二次**确认 |
| 未加引号的 `rm -rf $VAR` / `cd $VAR` | 通过 | 未发现 |
| **`detect_os` 失败容错** | **发现问题，已修** | `uninstall` / `check-update` 直接调 `detect_os`，在不识别的发行版上会因 `set -e` 中断——恰是最不该失败的两个操作。已统一为 `detect_os >/dev/null 2>&1 \|\| true` |

修复 commit：`d30afc5`（`bash -n` 通过，安装器断言 93/93）

---

## 模块 2：模板助手 —— ✅ 完成（含两次自我纠错）

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| `apply-dispatch` 动作映射 | 通过 | 7 个 ACTION → 7 个目标，与 `templates/*.sh` 一一对应 |
| 实际安装的文件 | 通过 | 服务器上 7 个均为 `-rwxr-xr-x root:root`；dispatch 是 Python，其余 bash |
| 被 `source` 的校验/渲染库 | 通过 | `apply-validate.sh` / `panel-validate.sh` / `panel-nginx.sh`，install.sh L1509/L1593-1594 安装 |
| socket 权限与状态 | 通过 | `srw-rw---- root:ovpnpanel`，`apply.socket: active` |
| **未知 / 缺失 ACTION** | 通过（真机注入） | `ACTION=__nonexistent__`、`ACTION=backup_extra`、`NOACTION=1`、空行 → 全部返回 `RESULT=failed`，进程不崩 |
| KEY=VALUE 协议一致性 | 通过 | 助手输出的 KEY 全部在面板 `wanted` 白名单内（20 个），字段不会静默丢失 |
| 校验不过不写文件 | 由既有测试覆盖 | `tests/test_apply_config.sh`（53）、`tests/test_panel_config.sh`（159）；本轮**未**单独真机复验 |

### 自我纠错（两次）

1. 先报"三个库文件缺失"——错在我查的是不带 `.sh` 的名字。install.sh 是以 `.sh` 后缀安装的，**不是缺陷**。
2. 再报"`RESTORED`/`TO`/`WARNING` 三个字段从未输出"——错在我的正则漏了多行 `printf` 与 `TO="%s"` 这种带引号写法。逐个 grep 后确认三者都有输出，**不是死字段**。

> 教训：审计脚本的正则也会骗人。凡是要写成"问题"的结论，必须回到源码逐条 grep 确认。

---

## 模块 3：后端 auth + security + ratelimit —— ⚠️ 完成核对，发现待修项

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 令牌含过期时间 | 通过 | `core/security.py` L21-24：`{"sub": ..., "exp": expire}`，HS256 |
| 过期校验 | 通过 | `api/security.py` L21 `jwt.decode(..., algorithms=["HS256"])`，jose 默认校验 `exp` |
| `sub` 缺失 / 签名错 | 通过 | L22-26 → 401 `Could not validate credentials` |
| 停用账号立即失效 | 通过 | L33-37 每次请求都查 `is_active`，不等令牌过期 |
| 口令哈希方案 | 通过 | `bcrypt_sha256`（`core/security.py` L9），避开 bcrypt 72 字节截断 |
| 登录限速 | 通过 | 键为 `用户名@IP`（跨 IP 无法锁死管理员）、成功后重置、锁定用 `monotonic`、`_MAX_KEYS` 上限防内存增长 |
| 2FA 失败也计入限速 | 通过 | `auth.py` 中 `_verify_otp_or_recovery` 失败后调用 `ratelimit.register_failure` |
| **改口令后旧令牌仍可用** | **已修复** | 见下 |

### 已修复：改口令后旧令牌立即失效（`users.password_changed_at` + 令牌 `iat`）

**原缺陷**：签发只写 `sub`+`exp`，校验只查签名与 `is_active`，所以改口令不影响已签发令牌。

**修法（5 处）**：
1. `models/user.py`：新增 `password_changed_at`（DateTime，可空）
2. `main.py`：`PRAGMA` 探测 + `ALTER TABLE users ADD COLUMN password_changed_at DATETIME`
3. `core/security.py`：令牌载荷加 `iat`
4. `api/security.py`：`password_changed_at` 存在且 `iat < password_changed_at` → 401
5. `crud/user.py`：`update()` 改口令时写 `password_changed_at`（**截到整秒**）

**踩到的坑（值得记）**：第一版用带微秒的时间戳，而 `iat` 是整数秒，导致**同一秒内签发的新令牌被误判为旧令牌**——19 个测试直接 401。改成整秒比较后通过。这类"精度不一致"的 bug 很难靠读代码发现，是测试逼出来的。

**测试**：`test_fixes.py` 新增两条回归断言（C16b 旧令牌 401 / C16c 新令牌 200），全部 **241 项通过**（原 239）。测试本身原来在改口令后继续复用旧令牌，属于依赖旧行为，已改为换用新令牌。

**真机验证（110.42.102.239，临时用户，未动 admin 口令）**：

```text
建临时用户 tk...（201）
用 P1 登录 -> 旧令牌调用 /api/2fa/status = 200
管理员 PATCH 该用户口令为 P2（200）
旧令牌调用 /api/2fa/status = 401 {"detail":"Password changed, please sign in again"}
用 P2 登录 -> 新令牌 = 200
清理：DELETE 200
```

### 发现：改口令 / 疑似泄露后，旧令牌在到期前始终有效

**证据（代码，非真机）**：
- `core/security.py` L21-24 只签 `sub` + `exp`，**没有 `iat`，也没有 token 版本号**
- `api/security.py` L14-38 只做「解签 → 查用户 → 查 `is_active`」，**不比对口令修改时间**
- 全仓库没有任何 token 黑名单 / 版本字段（`grep -r "token_version\|jti" backend/` 为空）

**影响**：管理员改口令后，之前泄露的令牌最长仍可用 `ACCESS_TOKEN_EXPIRE_MINUTES`（本机 .env 是 120 分钟）。停用账号能立刻生效（L33），但**改口令不能**——两者本该一致。

**建议修法（下一轮做）**：`users` 加 `password_changed_at`，签发时写 `iat`，校验时要求 `iat >= password_changed_at`。改动面：模型 + 迁移 + `create_access_token` + `get_current_user` + `change-password` 端点，共 5 处。

**为什么本轮没修**：需要动登录链路 + 表结构迁移，必须配真机验证（改口令后旧令牌应 401、新令牌可用），本轮上下文余量不足以保证验证到位。**不做半截的安全改动。**

---

## 模块 4：clients + certs + jobs —— ⚠️ 完成（修 1 处安全问题）

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 删除的清理范围 | 通过 | `cleanup_client_files` 删 `.ovpn` + CCD（`client_tasks.py` L41-50） |
| 会话断开容错 | 通过 | `kick_session` 受 `revoke_kicks_session` 控制，失败只告警（L53-61） |
| 同步/异步两种删除 | 通过 | `delete_client` 有 `wait` 参数，默认后台任务（`clients.py` L356-380） |
| **删除时吊销证书** | **发现问题，已修** | 见下 |
| 并发同名创建 / 任务失败回滚 | 见下（回滚已修；并发仅代码证据） |

### 已修复：回滚路径不吊销已签出的证书

`create_client_task` 的 `except` 分支调 `_rollback_client`，原来只做两件事：删数据库记录、删 `.ovpn` + CCD。但签发成功、导出 `.ovpn` 失败时，**证书已经在 PKI 里生效**——回滚不吊销，就和"删除客户端不吊销"是同一个洞。

修法：`_rollback_client(client_id, passphrase)` 改成**先吊销、再删记录**（此时 ORM 对象还挂在会话上），并把 `passphrase` 透传进来（CA 私钥带口令时，不传口令吊销会失败）。吊销失败只告警——证书可能压根没签发成功，那不是错误。

### 并发同名创建：只有代码证据，**没有实测**

- 端点层有查重：`clients.py` L238 按 `name`、L246 按 `common_name`，命中返回 400
- **没有核对**数据库层是否有唯一约束，也没有跑过并发压测
- 因此不能断言"并发安全"，只能说请求级查重存在

> 结论：模块 4 的两项收尾，回滚已修并跑通 241 断言；并发项标记为未验证。

### 已修复：.ovpn 不存在时删除客户端不吊销证书

**原代码**（`client_tasks.py`）：

```python
export_path = settings.openvpn_client_export_path / f"{common_name}.ovpn"
revoked = False
if export_path.exists():          # ← 把吊销绑在"导出文件存在"上
    certs.revoke_client_cert(db, client, passphrase=passphrase)
```

`cleanup_client_files` 只删 `.ovpn` 和 CCD，**没有任何其他地方吊销证书**。所以删掉一个"从没导出过配置"或"`.ovpn` 被手动删除"的客户端时：证书留在 `pki/index.txt` 里仍是 valid、CRL 不重新生成、`certificates` 表仍是 valid —— 手里留着旧配置文件的人**照样能连进来**，而面板里已经看不到这个客户端。

**修法**：无条件尝试吊销，失败只记日志（覆盖"本来没证书""已吊销"两种正常情况）。

**真机验证（110.42.102.239）**：

```text
建客户端 rv1792301003（202），触发导出后手动删掉 .ovpn（模拟"无导出文件"路径）
删除客户端 wait=true -> 200
index.txt: 该 CN 的 R 行数 = 1，V 行数 = 0        ← 确实吊销了
crl.pem mtime: 1792300974 -> 1792301012          ← CRL 确实重新生成
CCD 残留: 无    列表里是否还有: 0    剩余客户端数: 1（原有的 client-01）
```

> 注：脚本第 1 步用 `grep "Valid.*CN=$CN$"` 判断建后状态，返回 0 —— 是**我的匹配模式**与该 easy-rsa 版本的 `index.txt` 格式不合，不是失败；第 5 步用 `^R.*CN=$CN$` 得到了 1，是决定性证据。

3. 后端 auth + security + ratelimit
4. 后端 clients + certs + jobs
5. 后端 settings（server.conf 解析/写入/历史/回滚）
6. 后端 panel（TLS/备份/升级/网络）
7. 后端 dashboard + traffic + logs + health + notify + twofa + users + servers + share
8. 服务层 ccd / status_sync / alert_worker / backup_scheduler / selfcheck
9. 前端 App/router/i18n/http 拦截器
10. 前端 ClientsView + api/clients
11. 前端 Settings/PanelSettings/Users/Notify/Servers/Logs/Dashboard/Login
12. Docker（Dockerfile/compose/entrypoint/verify-offline）
13. 测试与 CI 覆盖
14. 文档与代码一致性

---

## 模块 5：后端 settings（server.conf 解析/写入/历史/回滚）—— 部分完成

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| Python 与 bash 的受管指令契约 | 通过（脚本逐字比对） | 26 条，集合与顺序完全一致 |
| 历史版本回滚是否先校验、先备份 | 未核对 | 下一轮 |
| 解析与写入往返（round-trip） | 由既有测试覆盖 | tests/test_apply_config.sh 53 项；本轮未另做真机往返 |

契约比对结果：

```text
python MANAGED_DIRECTIVES: 26
bash   MANAGED_ALLOW     : 26
only in python: []      only in bash: []
order identical: True
```

server_conf_writer.py L79 的注释要求"必须与 templates/apply-validate.sh 的 MANAGED_ALLOW 逐字一致"，这条契约确实成立。

### 一次概念错配（记录以免误报）

我第一次比对时把 Python 的 PUSH_ALLOW_PREFIXES（26 条）与 bash 的 PUSH_ALLOW（7 条）当成同一份东西，于是"发现"24 条只在 Python 侧。这是错的：

- bash 的 PUSH_ALLOW（7 条：dhcp-option / redirect-gateway / route / sndbuf / rcvbuf / ifconfig-ipv6 / block-outside-dns）是 push 子指令白名单
- Python 的 PUSH_ALLOW_PREFIXES（26 条）是托管块内允许出现的顶层指令名前缀，语义完全不同

不是缺陷；但命名相近，对后来的维护者是可读性隐患。
### 历史回滚路径核对（settings.py L541-570）

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 危险操作需要显式确认 | 通过 | L549-550：`confirm` 必须等于 `RESTORE`，否则 400 |
| 先校验再落盘 | 通过 | L554-557 `validate_candidate(text)`，失败直接 400 并带回原因；通过后才 `write_candidate` |
| 备份文件名防穿越 | 通过 | L475-490 `_read_backup`：正则白名单、拒绝符号链接、大小上限、缺失 404 |
| 替换失败可回滚 | 通过（助手侧） | L548 说明由 root 助手做"备份/替换/重启/回滚"；`RESULT != ok` 时把 REASON 写进审计 |
| 审计 | 通过 | L564-570 记录 `settings.restore` + 成功/失败原因 |

本轮**未发现缺陷**。模块 5 中"解析与写入往返"仍只由既有测试覆盖，未另做真机往返。
---

## 模块 6：后端 panel（TLS / 备份 / 恢复 / 升级 / 网络）—— 部分完成

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| TLS 模式取值校验 | 通过 | panel.py L78 `TLS_MODES = {"off","selfsigned","existing"}`；L242-248 校验拒绝其它值 |
| TLS 模式与证书路径一致性 | 通过 | L349-354：`TLS_MODE == "off"` 时**强制清空** TLS_CERT/TLS_KEY（否则助手拒绝，界面关不掉 TLS——我真机踩过，注释与代码都在） |
| `existing` 模式必须给证书与私钥 | 通过 | L362-364，缺一即 400 |
| 自定义放行模式必须给网段 | 通过 | L360-361，空列表 400 |
| 自签证书生成 | 通过（代码存在） | `templates/apply-panel-config.sh` 的 `ensure_selfsigned()`；本轮未真机切换验证 |
| 恢复前是否先备份当前状态 | 通过（助手输出可证） | `restore-backup.sh` 输出 `PRE_BACKUP=`（L43 失败路径、L162 成功路径），说明恢复前会先做一份 |
| 备份 / 升级 / 网络三项 | **未核对** | 下一轮 |

### 没能验证的（如实标注）

- **TLS 从 off 切到 selfsigned** 的完整真机流程本轮没跑（会重建 nginx 配置，需要窗口），只有代码证据
- 升级失败能否退回旧版本：只看到 `self-update.sh` 会返回 `FROM/TO/LOG`，没读回滚逻辑，也没实测
### 发现问题（未修）：在线升级没有回滚

**证据（代码）**：

- `install.sh` L2174-2205 `do_update`：`self_update` → `backup_current` → `deploy_panel` → `self_check` → `save_state`，**全程没有失败回滚**
- `backup_current` L2105-2118：只备份 `backend/.env` 与 `backend/data`（并保留最近 5 份），**不备份面板代码**、`/etc/nginx/conf.d/ovpnmanager.conf`、`/usr/local/lib/ovpnmanager/*` 模板
- `upgrade-worker.sh` L45/L66：安装器返回非 0 时只 `write_state failed`，不做恢复

**影响**：升级在中途失败（例如 `deploy_panel` 写了一半、`self_check` 不通过）时，系统停留在"代码已换、版本号与状态文件未更新、服务可能起不来"的中间态。此时 `backup_current` 里那份备份**不足以恢复旧版本**——它只有数据，没有代码。用户只能重跑安装器。

**建议修法（下一轮实现并真机验证）**：
1. `do_update` 开头把当前 `/usr/local/lib/ovpnmanager`、`/etc/nginx/conf.d/ovpnmanager.conf`、面板 tarball 解出的目录打成一个 `code-<stamp>.tar.gz`
2. 用 `trap ... ERR` 或显式检查，在 `deploy_panel`/`self_check` 失败时把代码目录还原并重启面板，状态写 `failed_rolled_back`
3. 至少要在 `INSTALLED_VERSION` 与真实运行版本不一致时给出明确提示，而不是留一个"看起来升级成功"的半成品

**为什么本轮没修**：这是改升级主链路，必须在真机上跑一次"故意让升级失败"的验证（例如给个坏 tarball），才能在下一轮证明回滚真的生效。半截的升级回滚比没有更危险。

### 模块 6 结论

TLS 相关全部通过（含关不掉的锁死修复）；恢复前有 PRE_BACKUP；**升级失败无回滚是唯一发现的问题，已记录未修**。备份/网络两项本轮只做了代码阅读，未真机验证。