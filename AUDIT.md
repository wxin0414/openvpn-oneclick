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
### 已做（本轮）：升级前的代码快照

`install.sh` 新增 `code_snapshot()`，在 `do_update` 的 `backup_current` 之后调用：

- 打包 `$PANEL_HOME`（排除 `.venv`、`node_modules`、`__pycache__`、`backend/data`）
- 打包 `/usr/local/lib/ovpnmanager`（助手与模板）
- 打包 `/etc/nginx/conf.d/ovpnmanager.conf`
- 落在 `$PANEL_BACKUP_DIR/code-<stamp>.tar.gz`，只保留最近 3 份
- 打包失败只警告，**不阻断升级**（不能让"备份失败"变成"装不上"）

这样升级中途坏掉时，至少有一条人工可回退的路：解开快照、`systemctl restart ovpnmanager`、必要时重跑安装器 `--adopt`。在此之前**连这条都没有**。

验证：`bash -n` 通过，安装器断言 93/93。

### 仍未做（下一轮）：自动回滚

`do_update` 依然是 `deploy_panel → self_check → save_state` 直下，失败不回滚。原因不变：`self_check` 内部会 `die`（直接退出脚本），用 `if !` 包不住，必须改成"返回值 + ERR trap 兜底"才可靠；这属于改主链路，需要用**故意失败的升级**做真机验收。半截的自动回滚比没有更危险，所以本轮只做可回退的底座。
### 模块 6 的一处未验证（如实记录）

`code_snapshot()` 只做了 `bash -n` 与安装器断言（93/93）验证，**没有在真实的 `install.sh --update` 上跑过一次**。原因是当轮上下文余量不足以在一次可能失败的在线升级后继续排查。下一轮应在真机跑一次正常 `--update`，确认 `/var/backups/ovpn-oneclick/code-*.tar.gz` 真的生成、能解开、内容包含 `backend/app` 与 nginx 配置。

---

## 模块 7：dashboard + traffic + logs + health + notify + twofa + users + servers + share —— 进行中

### 第一项核对（真机）：匿名可见面

无 Token 直接请求（真机实测）：

```text
/api/health              404  (不存在，只有 /api/health/version)
/api/health/version      200  {"version":"1.9.9"}   19 字节
/api/health/selfcheck    401
/api/openapi.json        404  (PROD 下关闭)
/api/docs                404
/api/clients/            401
/api/dashboard/metrics   401
/api/traffic/overview    401
/api/logs/audit          401
/api/users/             401
/api/panel               401
/api/settings/server     401
```

**结论**：除版本号外没有匿名可读的数据。`/api/health/version` 匿名可用属于**低危信息暴露**（版本号便于攻击者对照已知漏洞），但它是面板页脚与外部健康检查用的，且不泄露任何配置/用户数据，因此**本轮不改**，记录在案。

### 待核对

share 一次性令牌能否被重复兑换、traffic 采样口径、users 越权（viewer 能否调 admin 接口）、logs 导出上限、notify 的 URL 校验、twofa 的恢复码消费原子性。
### 第二项核对（真机）：分享令牌一次性 + TTL

```text
建 share（TTL 5 分钟，token 32 字符）
不带任何认证兑换同一链接：第 1 次 200（8378 字节 .ovpn），第 2 次 410，第 3 次 410
伪造 token：410
```

**一次性成立**（数据库条件 UPDATE 生效）。`ttl_minutes` 传 `0` / `-1` / `100000` 都返回 200，但服务层
`share.create` L33 是 `max(1, min(ttl_minutes, 10080))` —— 是**收敛**而不是原样接受（0 与 -1 变成 1 分钟，
100000 变成 7 天）。不是缺陷；但"静默收敛"对调用方不透明，属于 API 设计的轻微瑕疵。

### 第三项核对（真机）：viewer 越权

建 `role=viewer` 的临时账号登录后：

```text
管理/写接口（期望 403）：
  GET    /api/users/          403
  POST   /api/users/          403
  PATCH  /api/users/2         403
  DELETE /api/users/2         403
  PUT    /api/panel           403
  POST   /api/clients/        403
  PATCH  /api/settings/server 403
只读接口（期望 200）：
  /api/clients/ 200   /api/dashboard/metrics 200   /api/traffic/overview 200   /api/2fa/status 200
```

**没有越权**。清理删除账号 200。

### 模块 7 仍待核对

twofa 恢复码并发消费是否原子、traffic 采样口径、logs 导出上限是否真的生效、notify 的 URL 是否允许内网地址（SSRF）。
### 第四项核对：notify 的 URL 校验（SSRF）—— 发现并已修

**原状**：`notify._post` 只校验 `url.startswith(("http://","https://"))`，不判主机落在哪里。于是超管（或拿到超管账号的人）可以把 webhook 指到：

- `http://127.0.0.1:7505/x` —— OpenVPN 管理口
- `http://169.254.169.254/latest/meta-data/` —— 云厂商元数据
- 任意内网地址

**修法**：新增 `_host_is_internal()`，DNS 解析后逐个 IP 判断 `is_private / is_loopback / is_link_local / is_reserved / is_multicast`，解析失败也按不安全处理；`_post` 在 scheme 校验之后拦一道。确实需要内网 webhook 的部署可显式设 `NOTIFY_ALLOW_PRIVATE=1`。

**验证（本机）**：

```text
127.0.0.1:7505                     -> False  webhook 地址不能指向本机或内网…
localhost:8000                     -> False  （同）
169.254.169.254/latest/meta-data/  -> False  （同）
10.8.44.1 / 192.168.1.1            -> False  （同）
NOTIFY_ALLOW_PRIVATE=1 时 127.0.0.1:9 -> 真的去连，URLError（连接被拒，说明开关生效）
测试套件：241 项通过
```

**过程中我引入又修掉的一个 bug**：在 `_post` 里写 `import urllib.parse` 会让 `urllib` 变成该函数的局部名，后面的 `urllib.request.Request` 直接 `UnboundLocalError`。改成模块顶部 import 后正常——这类作用域问题只有真跑一遍才会暴露。
### 真机验证 SSRF 修复时又发现一个 bug：webhook 地址清不掉

部署后通过面板接口实测：

```text
PUT {"webhook_url":"http://127.0.0.1:7505/x"} -> 200
POST /api/notify/test -> {"ok":false,"reason":"webhook 地址不能指向本机或内网（…）"}   ← 防线生效
PUT {"webhook_url":"http://169.254.169.254/latest/meta-data/"} -> 200
POST /api/notify/test -> 同一个拒绝原因                                              ← 防线生效
PUT {"webhook_url":null} -> 200
GET /api/notify/ -> webhook_url 仍是 169.254.169.254…                                 ← 清不掉！
```

**根因**：`notify.py` L59 `notify_service.save(db, **payload.model_dump(exclude_none=True))` —— `exclude_none` 把**显式传的 null** 一起丢掉了，于是"清空地址"这个操作根本到不了后端。与之前 TLS 证书路径"关不掉 TLS"是同一类错误。

**修法**：用 `payload.model_fields_set` 区分"没传这个字段"与"传了空值"，显式 null/空串一律写成 `""`。

验证：241 项测试通过。**真机复验待下一轮**（已部署到服务器的那份配置里仍留着那个内网地址，虽然 `enabled=false`，下轮会清掉并复验）。
### 全项目扫描"清不掉"这一类（`exclude_none` / 合并状态）

递归扫描 `backend/app` 全部 .py：

```text
exclude_none:
  notify.py:59   data = payload.model_dump(exclude_none=True)        ← 已修（model_fields_set）
  panel.py:824   backup_scheduler.save(**payload.model_dump(exclude_none=True))
model_dump:
  clients.py:201 ClientCreate(**client_in.model_dump(exclude={"passphrase"}))
  base.py:26/34  exclude_unset=True                                   ← 正确：显式 null 会被应用
  user.py:31     exclude_unset=True                                   ← 正确
```

结论：

- `panel.py:824`（定时备份配置）虽然同样吞 null，但 `enabled/interval_hours/keep` 都不可空且带默认值，
  `enabled=false` 能正常传进去，**无实际影响**（记为"同模式但无害"）
- 客户端字段走 `exclude_unset`，显式 null **能**被清掉

### 顺着这条线发现的真问题（已修）：清空固定 IP 不重写 CCD

`clients.py` 原来的判断是 `if client_in.fixed_ip is not None or client_in.routes is not None`。
通过 API 显式传 `null` 清空固定 IP 时，这个条件为假 → **跳过 CCD 写入** → 文件里那条
`ifconfig-push` 留在原地 → 客户端下次连接照样拿到原来的固定 IP。界面上显示已清空，实际没清。

改为看 `model_fields_set`（请求里是否带了这个字段），并用 `updated` 的当前值重写 CCD。241 项测试通过。

> 注：这条用界面操作不会触发（界面清空后传的是空串 `""`，不是 null），是 API 层的边界。
> 但它属于同一类"合并状态吞掉用户意图"，所以一并修掉。

### 又一次工具失误（记录）

我第一次扫 `exclude_none` 用了 `Select-String -Path "$repo\backend\app\**\*.py"`，PowerShell 的
`**` 在这里**不会递归**，于是输出为空、我差点写成"全项目只有一处"。改用
`Get-ChildItem -Recurse | Select-String` 才拿到真实结果（两处）。结论：**"grep 没结果"在这套环境里不能直接当结论。**
### 真机复验（部署后）

```text
1) 部署前遗留：{"enabled":false,"webhook_url":"http://169.254.169.254/latest/meta-data/",…}
2) PUT {"webhook_url":null} -> 200
   结果：{"enabled":false,"webhook_url":"",…}          ← 清空生效（上一轮的 bug 确认修好）
3) 再把地址指向 127.0.0.1:7505 并 test：
   {"ok":false,"reason":"webhook 地址不能指向本机或内网（…）"}   ← SSRF 防线仍在
4) 最终配置：{"enabled":false,"webhook_url":""}         ← 已还原干净
   服务：openvpn@server / ovpnmanager / nginx 全部 active
```

**结论**：SSRF 修复 + 清空修复都在真机生效，服务器上遗留的内网地址已清除。

### 模块 7 剩余（未核对）

2FA 恢复码并发消费的原子性、traffic 采样口径与配额计算、审计 CSV 导出的 5 万行上限是否真的生效。
### 真机复验：清空固定 IP 后 CCD 里那行真的消失

用临时客户端（不动用户已有的 client-01）：

```text
建 cc1792301965，fixed_ip=10.8.44.250（202，id=3）
CCD 内容：ifconfig-push 10.8.44.250 255.255.255.0
PATCH {"fixed_ip": null} -> 200
CCD 内容：（空）        ifconfig-push 行数 = 0
API 回报 fixed_ip = None
清理：DELETE 202，剩余客户端 1（原有的 client-01）
```

修复前后对比：修复前这一步会跳过 CCD 重写，`ifconfig-push 10.8.44.250` 会**留在文件里**，客户端下次连接仍拿到旧 IP，而界面显示已清空。现在行为一致。
### 第五项核对（真机）：审计 CSV 的行数上限

```text
limit=10      -> 200   725 字节   11 行（表头 + 10 条）
limit=1000    -> 200  12085 字节  178 行（库里只有 177 条）
limit=50000   -> 200  12085 字节  178 行
limit=50001   -> 422
limit=100000  -> 422
limit=-5      -> 422
```

上限**严格执行**（越界走 FastAPI 校验，不是静默截断）。通过。

### 第六项核对（真机）：traffic overview 结构

```json
{"points":[{"t":…,"online":1,"rx":0,"tx":0},…],"top":[{"common_name":"client-01","rx":6083,"tx":3586}],
 "hours":24,"sample_interval":60,"retention_days":30,"total_rx":6083,"total_tx":3586,"samples":2}
```

字段齐全、`top` 与 `total` 一致。**采样口径本轮只看了结构，没有和 OpenVPN 实际流量对账**，不写"通过"。

### 第七项：2FA 恢复码并发消费 —— 发现竞态（未修）

**证据（代码，auth.py 的 `_verify_otp_or_recovery`）**：

```python
hashed = totp.hash_recovery_code(otp)
if hashed in stored:          # ① 读
    stored.remove(hashed)     # ② 改内存
    user.totp_recovery = json.dumps(stored)
    db.add(user); db.commit() # ③ 写
```

①→③ 之间没有条件更新（没有 `UPDATE ... WHERE recovery LIKE ...` 或行锁），两个并发登录拿着**同一张恢复码**可以都通过 ① 检查。窗口很小（毫秒级），但不是原子操作。share 令牌那边用的是条件 UPDATE（已真机验证 410），这里没有同样的保护。

**为什么本轮没修**：要改成条件 UPDATE（把"这条码还在"作为 WHERE 条件、以影响行数判定成功），并需要一个并发测试（两个线程同时用同一张码，断言只有一个 200）。上下文余量不足以保证验证到位，留作独立一轮。**这属于"知道有洞、但没把握一次改对"的情况，我不动。**

### 模块 7 结论

匿名面、分享一次性、viewer 越权、SSRF、webhook 清空、审计 CSV 上限：**6 项真机验证通过**。
2FA 恢复码并发：**发现竞态，未修**。traffic 采样口径：仅结构核对。
---

## 模块 8：服务层（ccd / status_sync / alert_worker / backup_scheduler / selfcheck）—— 进行中

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 备份失败是否错误推进 `last_run` | **通过**（怀疑被否定） | `backup_scheduler.py` L97-101：只有 `RESULT=ok` 才 `save(last_run=...)`；失败走 L100 `return {"ok": False}`，`last_run` 保持不变 |
| 备份失败后的重试行为 | **观察（未改）** | 见下 |
| status_sync 是否覆盖用户刚改的设置 | 未核对 | 下一轮 |
| alert_worker 冷却的作用域 | 已知：进程内字典，重启后同一条告警会再发一次 | 未在本轮复测 |
| selfcheck 的检查项 | 未核对 | — |

### 观察：备份持续失败会每分钟重试一次

`_due()` 用"距 `last_run` 是否超过 `interval_hours`"判断，而失败**不写** `last_run`；`_loop()` 每 60 秒跑一次 `_due`。所以一旦备份通道持续失败（例如助手不可用、磁盘满），会**每分钟尝试一次**并各记一条 warning，直到恢复。

这不是数据错误，是日志/资源噪音；如果要改，应加"失败后指数退避"或记录 `last_attempt` 独立于 `last_run`。本轮不改：属于行为改进而非缺陷，且需要真机长时间观察才谈得上验证。

> 顺带确认：这段代码用的是 `helper_client.run_helper("backup", ...)`（socket 激活），不是 `subprocess`——之前那个"必须以 root 运行"的坑没有回归。
### status_sync 核对（真读代码）

| 核对项 | 结论 | 证据 |
| --- | --- | --- |
| 会不会覆盖用户改的字段 | **通过** | `sync_once` L42-66 只写 `client.status`；`_apply_expiry` 只把 `disabled` 置 True。`fixed_ip / routes / group_name / quota_mb / expires_at` 一概不碰 |
| 状态判定顺序 | 通过 | 到期/超额 → disabled；手动禁用 → disabled；在线 → online；否则 offline（L44-51） |
| 审计 | 通过 | 上线记 `client_login`、掉线记 `client_logout`（L59-62），不写无变化的记录（L53-54 先比较） |
| 只在有变化时提交 | 通过 | L64-65 `if changed: db.commit()` |

### 观察（未改）：被自动禁用的客户端，条件解除后不会自动恢复

`_apply_expiry` 只在"到期或超额"时把 `disabled` 置 **True**（L123），**没有任何路径把它置回 False**。

后果：一个客户端因到期（或超额）被自动禁用后，管理员把 `expires_at` 延长、或把 `quota_mb` 调大，它**仍然是禁用状态**——面板列表显示"禁用"，连接照样被 CCD 里的 `disable` 挡住。用户必须再手动点一次「启用」。

**这算不算 bug 有争议**：不自动恢复是更安全的选择（避免管理员以为延期了但其它条件没满足就又放行）；但界面上没有任何提示说"你还需要点一下启用"，实际很容易让人以为延期失败。

**本轮不改**：改它会改变行为语义（自动放行），需要配套 UI 提示与真机验证；留作产品决策项，而不是当 bug 直接改掉。