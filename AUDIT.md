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
