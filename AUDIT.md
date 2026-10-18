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

**真机验证**：待下一轮（用临时用户跑一遍：登录→改口令→旧令牌应 401），本轮未做，不写"已验证"。

### 发现：改口令 / 疑似泄露后，旧令牌在到期前始终有效

**证据（代码，非真机）**：
- `core/security.py` L21-24 只签 `sub` + `exp`，**没有 `iat`，也没有 token 版本号**
- `api/security.py` L14-38 只做「解签 → 查用户 → 查 `is_active`」，**不比对口令修改时间**
- 全仓库没有任何 token 黑名单 / 版本字段（`grep -r "token_version\|jti" backend/` 为空）

**影响**：管理员改口令后，之前泄露的令牌最长仍可用 `ACCESS_TOKEN_EXPIRE_MINUTES`（本机 .env 是 120 分钟）。停用账号能立刻生效（L33），但**改口令不能**——两者本该一致。

**建议修法（下一轮做）**：`users` 加 `password_changed_at`，签发时写 `iat`，校验时要求 `iat >= password_changed_at`。改动面：模型 + 迁移 + `create_access_token` + `get_current_user` + `change-password` 端点，共 5 处。

**为什么本轮没修**：需要动登录链路 + 表结构迁移，必须配真机验证（改口令后旧令牌应 401、新令牌可用），本轮上下文余量不足以保证验证到位。**不做半截的安全改动。**

---

## 待核对模块

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
