# 更新日志

本文件记录 openvpn-oneclick 面向使用者的变更：修复、行为变更与安全相关调整。版本号以 `VERSION.txt` 为准，条目按时间倒序排列，分类沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的写法。

关于版本号：仓库历史中 `VERSION.txt` 由 `1.2.2` 直接跳到 `1.2.4`，没有 1.2.3 的独立提交，因此这里没有 `[1.2.3]` 条目。1.2.2 与 1.2.3 都因下列安装缺陷无法在 Debian 12 上完成安装，修复集中在 1.2.4。

## [1.8.1] — 2026-10-18

分组筛选与按组导出（第 7 项，后端部分）。

- `GET /api/clients/?group=xxx`：按分组过滤；`?group=__none__` 查未分组
- `GET /api/clients/export?group=xxx`：导出该组客户端**清单** CSV（带 UTF-8 BOM）
- 导出的是清单（id/名称/CN/分组/固定IP/路由/到期/配额/状态），**不是 .ovpn 打包**：
  `.ovpn` 在 root 生成的导出目录里，面板要读它得像备份那样单独放开目录权限，
  留到下一步做——先交付一个不依赖权限、立刻可用的导出

### 真机验证（110.42.102.239）

```text
建两个客户端：teamA / teamB
group=teamA -> total=1 names=['ga...'] groups=['teamA']
group=teamB -> total=1 names=['gb...'] groups=['teamB']
export?group=teamA -> HTTP 200，2 行 178 字节，表头带 BOM，
                      数据 id,name,common_name,group_name,...=teamA
清理：DELETE 202 两条，剩余 0，三服务 active
```

### 仍未完成（同一项内）

面板的分组输入、列表列、筛选下拉与导出按钮。
## [1.8.0] — 2026-10-18

暗色模式 + 英文导航（第 10 项，部分完成，范围如实标注）。

- 侧边栏底部新增主题切换按钮：给 `<html>` 加/去 `dark` 类，配合 Element Plus 官方暗色
  变量（`element-plus/theme-chalk/dark/css-vars.css`，已打进构建产物）
- 侧边栏底部新增语言切换（EN / 中），8 个菜单项有英文映射
- 两者都写 `localStorage`，刷新后保持

### **范围说明（不要误读为"多语言已完成"）**

英文**只覆盖导航菜单**。按钮、表单标签、提示语、错误信息仍是中文。
目标原文是「至少覆盖导航与主要按钮」——导航做到了，主要按钮尚未做到。

### 真机验证（110.42.102.239）

```text
bundle 含「切换到暗色」/「切换语言」/ Dashboard / Clients / Notifications 各 1 处
CSS 含 --el-bg-color（暗色变量已打包）
首页 200，三服务 active
```
## [1.7.4] — 2026-10-18

文档收尾：把「已完成 / 未完成」写清，避免文档跑在代码前面。

### 本轮周期内完成（全部经过真机 API 实测）

| 项 | 关键证据 |
| --- | --- |
| 面板 TLS 默认开启 | 新装默认自签 HTTPS；开→关往返验证（`ok=true`、https 200、http 400）；修复"开启后关不掉" |
| alerts 扩展 | 多来源（服务 / `apply.socket` / 管理接口 / 磁盘 / 证书），30 秒缓存 |
| Webhook 通知 | sink 收到完整 JSON；停 `apply.socket` 后收到 `critical`；通知设置页；冷却为进程内状态（重启重发一次，已注明） |
| 删除误导性 /api/servers | `POST`/`DELETE` 405，`GET` 200；路由 51→50 |
| 审计日志导出 CSV | 200、带 BOM、字段取自表结构 |
| 客户端到期与配额 | 到期：PATCH 过去时间 → 30 秒内 `disabled=True` + CCD `disable`；配额：1MB 配额 + 3MB 样本 → 同样自动禁用；面板可编辑 |
| 备份下载与定时备份 | 下载 200 / 76554 字节 / 78 条目、穿越 404、无令牌 401；`run-now` 份数 2→3；保留策略沙箱验证 |

### 仍未实现（如实记录）

| 项 | 原因 |
| --- | --- |
| 2FA / TOTP | 涉及登录流程改造（二步校验 + 恢复码 + 二维码），剩余轮次不足以做完并验证 |
| 客户端分组 / 标签 | 需要表结构 + 筛选 + 批量操作，未能开始 |
| 暗色模式 / 英文界面 | 需要改多处视图与 i18n 层，未能开始 |
| 通知 SMTP 渠道 | 目标中标为"可选"，只做了 Webhook |

### 已知限制

- 通知的冷却去重是**进程内内存**，面板重启后同一条告警会重发一次
- 定时备份的"到点自动执行"未实测（需等一个完整间隔周期），`run-now` 已验证
- 面板默认仍是 HTTP（`panel.state` 保留 `off`），需手动在「面板设置 → TLS」开启
## [1.7.3] — 2026-10-18

修复备份下载的真机 500（目录组被覆盖）。

### 现象

界面和接口都正常，但点下载返回 `Internal Server Error`，日志是：

```text
PermissionError: [Errno 13] Permission denied: '/var/backups/ovpn-oneclick/manual-....tar.gz'
```

### 根因

文件是 `-rw-r----- root:ovpnpanel`、面板用户也确实在 `ovpnpanel` 组里，但**目录**是
`drwxr-x--- root:root` —— 组不可进入，所以文件权限再对也读不到。

目录组被打回 root 的原因：`install.sh` 每次安装都会
`chown root:root "$STATE_DIR" "$PANEL_TLS_DIR" "$PANEL_BACKUP_DIR"`，把助手里的
`chgrp ovpnpanel` 覆盖掉。**只在助手侧改权限是不够的，安装器才是每次都会跑的那个。**

### 改法

在安装器的 chown 之后补 `chgrp "$PANEL_USER" "$PANEL_BACKUP_DIR"` 与 `chmod 0750`。

### 真机验证（110.42.102.239）

```text
目录 -> drwxr-x--- root:ovpnpanel
面板可进入目录
下载 manual-20261018-112237.tar.gz -> HTTP 200  size=76554  包内 78 条目
version 1.7.3，三服务 active
```
## [1.7.2] — 2026-10-18

备份的界面入口（第 8 项收尾）。

- 备份列表每行新增「下载」按钮：走 `blob` 请求而不是 `<a href>`——接口要 Bearer 令牌，
  普通链接不会带 `Authorization`，直接点必然 401
- 新增「定时备份」卡片：启用开关、间隔（1~168 小时）、保留份数（1~200）、
  「立即执行一次」与「上次执行」时间
- 顺手修掉界面里「权限 0600」的过时说明（实际已是 `0640`，属组 ovpnpanel）

前端构建通过（10.12s）。
## [1.7.1] — 2026-10-18

修复定时备份在真机上的两个致命问题。

### 1) 调度器调错了通道（真机抓到）

第一版用 `subprocess.run([backup-now])` 直接调助手，真机返回：

```text
RESULT=failed / REASON=必须以 root 运行（通过 socket 激活）
```

助手只接受 **socket 激活**。改为走 `helper_client.run_helper("backup", ...)`，
与「立即备份」完全同一条通道。

### 2) 保留份数传不进 socket 激活的服务

socket 激活的进程拿不到调用方的环境变量，所以 `KEEP` 传不过去。改为调度器写
`data/backup-keep`，助手启动时读它（我写的助手守护在 `KEEP="${KEEP:-10}"` **之后**才生效——
第一次插在默认值之前，被默认值覆盖了）。

### 3) 我自己的低级错误

插入助手脚本时把 `fi` 和下一行粘成了 `fiBACKUP_DIR=...`；清理时又用 `-like` 匹配
`[ -z ... ]`，`[` 在 PowerShell 的 `-like` 里是字符类，导致残留一个孤立的 `if`，
`bash -n` 报 "unexpected end of file"。改用 `-ceq` 精确匹配后清理干净。

### 真机验证（110.42.102.239）

- 调度配置读写：`GET/PUT /api/panel/backup-schedule` 正常
- 保留策略：在 `/tmp` 沙箱造 4 个假备份，`KEEP=2` 后剩 2 个（不碰真实备份）
- 备份份数由 2 → 2：因为 `run-now` 当时失败（问题 1），修复后待下轮复验
## [1.7.0] — 2026-10-18

定时备份与保留策略可配（第 8 项第二部分）。

- `services/backup_scheduler.py`：每 60 秒检查一次是否到期，到期就调用与「立即备份」
  **同一个** root 助手 `backup-now`，通过环境变量 `KEEP` 传保留份数——保留策略只有
  一套实现，不会出现手动和定时行为不一致
- 配置存 `data/backup-schedule.json`（`enabled` / `interval_hours` 1~168 / `keep` 1~200 /
  `last_run`）。只有三个字段，不值得为它加一次表结构迁移
- 接口：`GET/PUT /api/panel/backup-schedule`、`POST /api/panel/backup-schedule/run-now`，
  均限超级管理员
- 线程崩溃不影响面板：循环体整体包 try，失败只记日志；`BACKUP_SCHEDULER_ENABLED=false` 可关
- 顺手修掉一处过时文案：备份接口曾提示「文件权限 0600」，实际已改为 `0640`（属组 ovpnpanel）

### 本地验证

```text
路由 56 条；/api/panel/backup-schedule 与 /run-now 均已注册；后端 239 断言通过
```
## [1.6.3] — 2026-10-18

备份下载（第 8 项第一部分）。

- 新增 `GET /api/panel/backups/{name}/download`（超级管理员）
- 校验两层：文件名必须严格匹配 `manual-\d{8}-\d{6}\.tar\.gz`，再做 realpath 包含性检查。
  备份包里是 CA 私钥，能读走等于所有客户端证书作废，所以不放宽这条
- **权限前提**：原来备份目录 `0700`、文件 `0600` 全是 root，面板进程根本读不到。
  改为目录 `0750`、文件 `0640` 并加入 `ovpnpanel` 组——面板本来就能通过 root 助手做
  恢复（破坏性更强），因此读权限不构成新的越权面

### 本地验证

```text
../etc/passwd                              -> 400 备份文件名不合法
manual-2026.tar.gz                         -> 400 备份文件名不合法
manual-20260101-000000.tar.gz/../x         -> 400
manual-20260101-000000.tar.gz（不存在）    -> 404 备份不存在
路由 54 条，后端 239 断言通过
```

### 尚未完成（同一项内）

定时备份（调度 + 保留策略可配）与前端下载按钮。
## [1.6.2] — 2026-10-18

客户端到期与配额的面板字段（第 4 项收尾）。

- 客户端对话框新增「到期时间」（留空=永不过期，`YYYY-MM-DDTHH:mm:ss`）与
  「流量配额 MB」（留空=不限）
- 创建与更新的请求载荷都带上这两个字段；列表新增「到期」「配额」两列
- 此前这两项只能通过 API 设置，界面上完全看不到
## [1.6.1] — 2026-10-18

客户端流量配额按量踢线（第 4 项，配额部分闭环）。

- `status_sync._quota_exceeded()`：按 `traffic_samples` 累加**正增量**得到累计用量，
  超过 `quota_mb` 即判定超额。不能直接用最后一条样本——管理接口给的是**会话累计值**，
  客户端断开重连后计数从 0 重来，用最后一条会严重低估
- 只有设了 `quota_mb` 的客户端才查库，避免给所有客户端加一次扫描
- 超额与到期共用同一条处置路径：`disabled` 落库 + 写 CCD `disable` + 断开在线会话

### 真机验证（110.42.102.239）

```text
设 quota_mb=1，插入一条 3MB 的 traffic_samples
等待状态同步（约 30s） -> disabled=True
CCD                     -> disable|
清理                     -> DELETE 202、样本已删、三服务 active
```

### 尚未完成（同一项内）

面板表单与列表展示：目前 `expires_at` / `quota_mb` 只能通过 API 设置。
## [1.6.0] — 2026-10-18

客户端到期时间（第 4 项，到期部分闭环）。

- `clients` 表新增 `expires_at`(DATETIME) 与 `quota_mb`(INTEGER)，均可空；
  可空意味着升级后原有客户端行为不变（不过期、不限量）
- `_ensure_client_quota_columns()`：`create_all` 不会给已有表加列，用 `PRAGMA table_info`
  探测后 `ALTER TABLE`。不这么做，升级上来的机器一读新列就 `no such column`，客户端页直接打不开
- 创建/更新/响应三类 schema 都带上这两个字段（第一版只给 `ClientUpdate` 加了注释没加字段，
  PATCH 被 Pydantic 静默丢弃、`expires_at` 回读是 `None`——真机验证时才发现）
- 生效逻辑 `status_sync._apply_expiry()`：借用每 30 秒一次的既有同步循环（不新增调度器），
  发现过期就 `disabled=True` 落库 + 写 CCD 的 `disable` + 断开在线会话；只做一次副作用，
  避免每轮重写 CCD

### 真机验证（110.42.102.239）

```text
PATCH expires_at = 1 小时前  -> 返回该值，disabled=False
等待状态同步（约 30s）       -> disabled=True
CCD 内容                     -> disable|
清理                         -> DELETE 202，服务全 active
```

### 尚未完成（同一项内的后续）

- **流量配额 `quota_mb` 的按量踢线**：字段已存，但还没有按客户端累计流量的计算与触发
- 面板表单字段与列表展示（目前只能通过 API 设置到期时间）
## [1.5.2] — 2026-10-18

审计日志导出 CSV（第 9 项）。

- 新增 `GET /api/logs/audit/export?limit=&action=`，返回 `text/csv` +
  `Content-Disposition: attachment`，文件名带时间戳
- **文件头带 UTF-8 BOM**：不加的话 Excel 打开中文全是乱码（用户实际就是这么用的）
- 列名取自表结构（`model.__table__.columns`），以后加字段导出会跟着变，不会悄悄少列
- 鉴权沿用路由级 `get_current_user`；`limit` 上限 50000，避免一次拉爆内存
## [1.5.1] — 2026-10-18

通知设置页（补上第 3 项缺的前端）。

- 新增侧边栏「通知设置」页（`NotifyView.vue`）与 `api/notify.js`
- 可配置：启用开关、Webhook 地址、最低推送等级（critical / warning）、冷却秒数
- 页面上直接「发送测试」，结果按真实 HTTP 结果提示，不是只显示"已发送"
- 页面写明会把什么推给你（服务/助手通道/磁盘/证书）、不推什么（告警恢复不发信），
  以及冷却状态在进程内、重启后会重发一次这条限制
- 此前只能用 API 配 webhook，现在普通管理员也能在界面完成
## [1.5.0] — 2026-10-18

告警与通知闭环（第 2、3 项）。

### alerts 扩展（第 2 项）

`/api/dashboard/metrics` 的 `alerts` 此前只有「证书 30 天内到期」一个来源，服务挂了、
磁盘快满、特权助手通道失效都看不见。现在复用自检项汇总，返回：

```json
{"count": 2, "critical": 1, "warning": 1,
 "items": [{"level": "critical", "key": "helper_channel", "message": "...", "hint": "..."}]}
```

来源：OpenVPN / 面板 / nginx 服务、`apply.socket`、管理接口、磁盘、证书到期。
带 30 秒缓存（仪表盘会被反复打开，不能让每次请求都 fork `systemctl`），整体包在 try 里，
告警自身异常不会把仪表盘打挂。

### Webhook 通知（第 3 项）

- `POST /api/notify/test`：手动测试发送（真机验证 sink 收到完整 JSON，中文未乱码）
- `GET/PUT /api/notify/`：配置 `enabled / webhook_url / min_level / cooldown_seconds`，
  仅超级管理员；无令牌访问返回 401
- `services/notify.py`：只允许 `http(s)://`、5 秒超时、**所有异常吞掉只记日志**——
  推送失败绝不影响主流程
- `services/alert_worker.py`：每 `ALERT_CHECK_SECONDS`（默认 300）算一次告警，
  逐条走 `send_alert_once`；循环体整体包 try，单次失败线程不退出

### 已知限制（不要当成 bug 忽略）

**冷却去重是进程内内存，面板重启后同一条告警会重发一次。** 真机演练用两次独立
`python -c` 进程跑巡检，看到 sink 从 2 条涨到 4 条——这不是冷却失效，而是进程内状态不跨进程。
真正的作用范围是那一个长期运行的巡检线程。

### 真机演练（110.42.102.239）

```text
停掉 apply.socket -> inactive
巡检一轮          -> sink 收到 critical|helper_channel、warning|panel_tls
恢复 apply.socket -> active
```

另外两次巡检都打印了 `management 接口熔断 5.0s：'status 3' 失败：timed out`——
与 `apply.socket` 无关（那是 127.0.0.1:7505 的 TCP 接口），**尚未定位，不能下结论**。

### 仍未完成（不隐瞒）

- 通知的前端配置卡片（现在只能用 API 配）
- SMTP 渠道（目标里标为可选）
- 第 4、6、7、8、9、10 项（客户端配额与到期、2FA、分组、定时备份、审计导出、暗色/多语言）
## [1.4.9] — 2026-10-18

面板 TLS：新装默认开启自签 HTTPS；修复"开了就关不掉"的锁死 bug。

### 新增

- **新装默认 `PANEL_TLS_MODE=selfsigned`**：管理员口令与令牌不再默认走明文 HTTP。
  已装过的机器仍以 `panel.state` 为准，升级不会突然切换协议。
- **首次安装时生成自签证书**：CN 优先取域名，否则公网 IP，再否则主机名；SAN 用
  `IP:`（IP 形态）或 `DNS:`。文件名与 SAN 规则与助手 `apply-panel-config.sh` 的
  `ensure_selfsigned()` 完全一致（`panel-selfsigned.crt|key`，私钥 0600、目录 0700）。
  `openssl` 版本过老不支持 `-addext` 时自动退化为不带 SAN 的证书。
- 自检新增「面板 TLS」一项：`off` 时给 warn 并说明怎么开（`selfsigned` / `existing` 时 ok）。

### 修复 — 开启 TLS 后无法从界面关掉（真机踩到）

面板 PATCH 时 `merged` 会**沿用 state 里保留的证书路径**，而助手的校验规定
"`mode=off` 不接受自定义证书路径"，于是 `{"tls_mode":"off"}` 被拒绝：

```text
面板设置未生效 / reason: TLS 模式为 off 时不接受自定义证书路径
```

结果：面板一旦开启 TLS，从界面上就关不回去了（只能显式传空字符串或重跑安装器）。
现在 `TLS_MODE=off` 时会强制清空 `TLS_CERT`/`TLS_KEY` 再写候选。

### 真机验证（110.42.102.239）

开启：`{"tls_mode":"selfsigned"}` → 证书 `CN=110.42.102.239`、`SAN=IP Address:110.42.102.239`
→ `https` 首页 **200**、`https` 登录 **200**、`http` 请求返回 **400**（nginx 提示
"plain HTTP request was sent to HTTPS port"）→ 自检「面板 TLS」为 `ok`。
关闭：显式空证书路径成功恢复 `http 200`。

> 我**没有**把你这台机器留在 HTTPS：那会改变你现在的访问地址并在浏览器弹自签证书警告。
> 需要时在「面板设置 → TLS」选「自签证书」保存即可（一次点击，可随时改回 off）。
> 本次修复（off 时清空证书路径）尚未在真机做一次完整的开→关往返验证，下一轮补。
## [1.4.8] — 2026-10-18

仪表盘告警扩展 + 删除误导性的多服务器接口。

### 修复 — alerts 只统计证书到期

`/api/dashboard/metrics` 的 `alerts` 此前只有"证书 30 天内到期"一个来源。**服务挂了、
磁盘快满、特权助手通道失效**这些更急的情况都不计入，用户从仪表盘上完全看不出来。

现在复用自检的检查项汇总，返回结构升级为：

```json
{"count": 2, "critical": 1, "warning": 1,
 "items": [{"level": "critical", "key": "openvpn_service", "message": "...", "hint": "..."}]}
```

来源包括：OpenVPN / 面板 / nginx 服务、特权助手通道（apply.socket）、管理接口、磁盘空间、
证书到期。带 30 秒缓存——仪表盘会被反复打开，不能让每次请求都去 fork 若干 `systemctl`。
告警自身异常也不会把仪表盘打挂（整体包在 try 里）。

### 修复 — 删除 POST/DELETE /api/servers

这两个路由存在，但**没有任何地方会真正管理第二台机器**（`server_endpoint()` 只取第一条记录），
留着等于暗示"支持多机"。已删除，并在源码注释与 README 里写明**仅支持单机**。
现在 `/api/servers` 只剩 `GET /`、`PATCH /{id}`、`GET|PUT /config`（共 50 条路由）。
## [1.4.7] — 2026-10-18

补齐三处"有后端没界面"与一个**假功能**。

### 修复 — 「禁用客户端」此前是假功能

`clients.disabled` 字段只被 `status_sync` 读来做**状态显示**：没有任何接口能设置它，
CCD 也从不写 `disable`，所以"禁用"之后**客户端照样能连**。这类"看起来禁用了其实没禁"
比明确缺功能更危险。

现在的行为：

- 新增 `PATCH /api/clients/{id}` 的 `disabled` 参数
- 禁用 → 往该客户端的 CCD 写 `disable`（OpenVPN 的 ccd 支持，连接时直接踢掉），
  并立即断开在线会话
- 启用 → 重写 CCD（保留 `ifconfig-push`/`iroute`，只去掉 `disable`）
- 响应模型补上 `disabled` 字段（否则前端拿不到真实状态，按钮永远显示同一个值）
- 客户端列表新增 **禁用/启用** 动作，带二次确认

真机验证（110.42.102.239）：
创建后 `disabled=False` → 禁用后 `True` 且 CCD 为 `disable` → 启用后 `False` 且 CCD 清空
→ 同时设固定 IP 时 CCD 两行并存（`disable` + `ifconfig-push`）→ OpenVPN 全程 active。

### 新增 — 用户管理页

后端 `/api/users` 四个路由一直存在，但**前端既没有 `api/users.js` 也没有页面**，
导致上一版做出来的 `viewer` 只读角色只能靠 curl 建账号。现在补齐：

- `frontend/src/api/users.js`、`frontend/src/views/UsersView.vue`、路由 `/users`、侧边栏入口
- 列表（用户名/角色/状态/创建时间）、新增（可选 admin 或 viewer）、重置口令、停用/启用、删除
- 同页提供 **修改我的口令**（`POST /api/auth/change-password`）——此前 `viewer` 根本没有
  改自己口令的入口（`/panel/admin-credentials` 是超管且被中间件挡）

### 修复 — 「服务器管理」页的误导

后端 `/api/servers` 有 `POST`/`DELETE`，但前端只有"保存配置"，库里也永远只有本机一条记录。
页面顶部现在明确写着 **当前版本仅管理本机服务器**，并说明地址/端口只用于生成客户端配置的
`remote` 行 —— 不再让人以为能加机器。

### 过程记录

`clients.js` 里我把注释写成了 `#`（Python 风格），Vite 直接报 `Unexpected character '：'`
把整包构建打断——属于低级失误，已改。## [1.4.6] — 2026-10-18

安全修复：客户端名称（`common_name`）缺少校验，可导致**路径穿越写文件**与**参数注入**。

### 漏洞

`common_name` 只限了长度（`max_length=100`），而这个值会**直接拼进文件路径**并作为
EasyRSA 的命令行参数：

```text
pki/issued/<cn>.crt、pki/private/<cn>.key、ccd/<cn>     easyrsa build-client-full <cn>
```

实测（本地复现，未在生产环境触发）：`CCD_PATH=/tmp/x` 时把 `common_name` 设为
`../../escaped-by-cn`，`ccd.write_ccd()` 返回的路径是 `/tmp/x/../../escaped-by-cn`
且**文件确实被创建在 CCD 目录之外两层的可写目录**。即：任何能创建客户端的账号，都能
以面板用户身份在它可写的范围内写任意路径。另外以 `-` 开头的名字会被 easyrsa 当成选项。

### 修复

新增 `backend/app/services/names.py` 作为**唯一校验入口**，并在四层各设一道：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 请求校验 | `schemas/client.py` 的 `pattern` + `field_validator` | 非法名直接 422 |
| 路径拼接 | `services/ccd.py`、`services/certs.py` 的 `ensure_safe_name()` | 兜底，绕过 API 也不会写出去 |
| 命令行 | `install.sh` 的 `validate_client_name()` | `--addclient` 等以 root 执行，入口挡住 |
| 规则 | `NAME_PATTERN` | 前后端与助手共用同一套 |

**规则刻意宽到能用**：允许中文、字母、数字、空格等正常字符；只禁 `/`、`\`、NUL/控制字符、
连续的 `..`、以 `-` 或 `.` 开头、纯点号、长度 > 64。即"文件名安全 + 不是命令行选项"
两条底线，而不是把字符集收窄成 ASCII（那会让中文客户端名直接不可用，属于过度限制）。

> 踩过：最初把 `(?!.*\.\.)` 写进 pydantic 的 `pattern`，它的正则引擎是 Rust 的、
> **不支持前瞻**，直接报 `look-around is not supported`。改为 pattern 只表达字符集、
> `..` 交给 `field_validator`。

### 顺带修掉

- **登录限流表无上限增长**：键是"用户名@IP"，攻击者用随机用户名可把两张 dict 刷到内存耗尽。
  加了 `_MAX_KEYS=4096` 与过期清理。（跨 IP 锁死管理员不成立——键含 IP，已验证。）

### 复核后确认不是问题（避免误报）

- `DEPLOY_ENV` 模板里就是 `PROD`，线上 `/docs`、`/api/openapi.json`、`/redoc` 实测都是 **404**
- JWT 带 `exp`；登录有限速且键含 IP，无法从其他 IP 锁死管理员
- 前端 `localStorage` 存 token 是已知取舍（无 `v-html`，XSS 面很小），未改动
## [1.4.5] — 2026-10-18

文档重整 + 修复页脚版本号永不更新。

### 修复 — 面板右下角版本号停在旧值

页脚读的是 `frontend/package.json` 的 `version`，而发版流程只改 `VERSION.txt` 与
`install.sh` 的 `SCRIPT_VERSION`。于是页脚长期停在 `1.4.0`，实际已经是 1.4.4 —— 属于
"只有用户会看到、作者从来不看"的那类偏差。

改法是**消除多处来源**，而不是"记得改"：

- `pack.sh` 在打包时把版本写进 `backend/app/_build_version.py`（`VERSION = "x.y.z"`），
  后端 `app/version.py` 优先读它，读不到才去探测文件。**先试过"把 VERSION.txt 拷进制品"，
  但安装脚本在 `--adopt` 路径上没拷到，页脚仍显示 unknown**，所以改成编译期常量——
  它不会因为安装路径变化而丢失
- 新增 `backend/app/version.py`：从制品/源码布局读同一个文件，导出 `APP_VERSION`
- 新增 `GET /api/health/version`（无需登录，只返回版本字符串）
- 前端页脚改为启动时向后端取；`package.json` 仅作请求失败时的兜底
- 新增 `tests/check_versions.py` 并接入 CI：`VERSION.txt` / `SCRIPT_VERSION` /
  `package.json` 三者不一致就失败

### 文档

- `README.md` 重写：语言切换行、CI/Release/License 徽章、一段讲清"和只装脚本的项目区别"、
  特性清单、一键安装（含国内网络下载不动时的替代方案）、**面板能改什么**表格、
  日常命令、安全模型（`<details>` 折叠：`NoNewPrivileges` 与 socket 激活链路）、
  Docker、明确未实现的功能、已验证环境、致谢。
- 新增 `README.en.md`（英文镜像）。
- **删除 `docs/` 下 5 个过时文档与 6 张截图**（firewalld 端口、企业内网 TCP 指南、
  UI 设计方案、客户端说明、内网双向访问）：内容已过时，且与 README 重复。
## [1.4.4] — 2026-10-18

修复：用 `--adopt` / `--update` 升级时，安装器自身的副本不更新——会导致"面板是新版、副本是旧版"。

### 问题

面板的「在线升级」优先使用 `/usr/local/lib/ovpnmanager/install.sh` 这份本地副本（服务器到
`raw.githubusercontent.com` 常常不稳，只有副本缺失时才回退下载）。但那份副本此前只在**全新安装**
的部署流程里复制一次；用 `--adopt` / `--update` 升级时走不到那条分支。

后果：面板已经是 1.4.3，副本仍停在 1.3.2 —— **下次在面板点「在线升级」会把面板装回 1.3.2**。
实测在一台机器上确认了这个状态。

### 改法

新增 `sync_self_copy()`，在脚本入口处**无条件**调用（仅 root、仅当内容不同才写）：

```bash
if [ -f "$dst" ] && cmp -s "$0" "$dst"; then return 0; fi   # 内容相同就不写
```

与具体模式无关，`--auto` / `--adopt` / `--update` / 单独跑 `--addclient` 都会同步。

### 验证

真机（110.42.102.239）用 `--auto --adopt` 部署，日志出现「已同步安装器副本到
/usr/local/lib/ovpnmanager/install.sh」，副本 `SCRIPT_VERSION="1.4.4"`，面板 active。
安装器断言 93/93。

> 注：1.4.3 当时没有打 tag；本版把两者的内容一并纳入 `v1.4.4`。
## [1.4.3] — 2026-10-18

面板内按平台下载 OpenVPN 客户端。

### 新增

- `frontend/src/components/ClientDownloads.vue`：Windows / Android / iOS / macOS / Linux
  五个平台卡片，指向**官方**分发地址。
- 「客户端管理」页顶部新增 **客户端下载** 按钮；分享弹窗里也有「按平台下载 OpenVPN 客户端」入口，
  让"拿配置"和"装客户端"在同一处完成。

### 两个刻意的取舍

1. **iOS 不写 App Store 数字 ID**：只给 `openvpn.net/client`，由官网按平台跳转。没把握的
   深链接比不给更糟（装错或打不开）。
2. **只给官方来源**：Android 用 `net.openvpn.openvpn` 的 Play 地址，macOS 额外给 Tunnelblick，
   Linux 给社区下载页。面板里也明确提示不要用第三方「一键连 VPN」类应用导入本服务的配置。
## [1.4.2] — 2026-10-18

Docker：修掉一个"构建成功、运行必坏"的缺陷；并记录一次我把生产配置覆盖的事故。

### 修复 — 镜像里助手文件名带 .sh，运行期永远找不到

`Dockerfile` 原来用 `COPY templates/apply-config.sh ... /usr/local/lib/ovpnmanager/`，
装出来的是 `apply-config.sh`；但面板的 `APPLY_CONFIG_BIN` 与 `/etc/sudoers.d/ovpnmanager`
都指向**不带后缀**的 `/usr/local/lib/ovpnmanager/apply-config`。

后果：镜像能构建成功，**运行时所有"改配置"功能静默失效**（面板找不到助手、sudoers 规则也匹配不上）。

改法：`COPY templates/ /tmp/templates/` 后用 `install -m 0755 "$name.sh" ".../$name"` 逐个改名安装，
与宿主机安装脚本的行为一致。已在 `verify-offline.sh` 里加了三条静态断言防止复发：
安装循环引用的助手必须存在于 `templates/`；sudoers 放行的每个路径必须有对应安装名；
面板源码里期望的路径必须与镜像安装名一致。

### 事故记录 — 我覆盖了生产 `server.conf`

`verify-offline.sh` 早期版本用 `sed -n '/^log() {/,/^}$/p'` 从 entrypoint 里抽函数，
这个范围会一路吞到很远的 `}`，把脚本顶部的 `OPENVPN_DIR=/etc/openvpn` 也 `eval` 进来，
于是 `SERVER_CONF` 指向了**真实路径**；脚本接着 `rm -f` 并生成 31 行模板，覆盖了生产配置。

发现与恢复：OpenVPN 进程仍持有旧配置所以在跑，但重启就会读到模板。已从最新
`server.conf.bak.20261018-002838`（67 行、托管块完整）恢复、重启，并通过自检 11/11。

改法：① 用 `awk` 精确圈定函数体（`/^init_conf\(\) \{/{f=1} f{print} f&&/^\}$/{exit}`）；
② 在**写入之前**加安全闸门——`SERVER_CONF` 不落在临时目录就直接 `exit 2`。
教训：会写文件的自检脚本必须"先拒绝、再执行"，事后断言来不及。
## [1.4.1] — 2026-10-18

Docker：补上"不需要 Docker 也能做的验证"。

### 新增

- `docker/verify-offline.sh`：从真实 `entrypoint.sh` 抽出 `init_conf` 生成配置，再用
  `openvpn --help` 的选项表逐条核对；用**面板自己的解析器**确认托管块与 29 个可编辑项；
  用真机 `nginx -t` 校验容器 `nginx.conf`；解析 `docker-compose.yml` 关键字段。
  任何人（含 CI）都能复跑，不需要 Docker 守护进程。

### 实测结果（110.42.102.239）

- `init_conf` 生成 31 行配置；openvpn 认识 243 个选项
- 面板解析：`port=1194 proto=udp net=10.8.44.0 topology=subnet`，**29 个可编辑项**，
  `dns=['8.8.8.8','8.8.4.4']`，`tunnel=full`
- `nginx -t`：`syntax is ok / test is successful`
- compose：`services=['ovpn'] ports=['1194:1194/udp','5698:5698/tcp'] volumes=3 cap_add=['NET_ADMIN']`

### 仍然未验证（如实记录）

镜像构建、容器启动、运行期行为——都需要 Docker 守护进程，而目标机未安装。
README 已把"已验证/未验证"分开写清，不再用一句含糊的"尚未验证"盖过去。
## [1.4.0] — 2026-10-18

只读运维角色（viewer）。

### 新增

- `users.role` 列（`admin` / `viewer`），默认 `admin`；升级上来的机器自动 `ALTER TABLE` 补列，
  原有账号权限不变。
- 全局中间件 `ReadOnlyGuard`：`viewer` 的所有写请求返回 **403**（放行 `POST /api/auth/login`）。

### 为什么用中间件而不是逐端点依赖

写操作分散在十几个路由文件里，逐个加依赖一定会漏——尤其是**以后新增的端点**。
中间件只认两件事："HTTP 方法是不是写"、"路径是不是 `/api/`"，新增路由自动受保护。
代价是每个写请求多一次 JWT 解码与一次用户查询（只读请求不受影响）。

### 读接口的权限拆分

`viewer` 要能**看到**配置才谈得上"只读运维"，但原先 `GET /api/settings/server` 与
`/api/panel*` 的读接口都内联要求超管（或用了 `ensure_superuser`），viewer 连页面都打不开。
现拆分为：**读：登录即可；写：超管 + 中间件双保险**。

### 过程中修掉的三个 bug（都是真机验证才暴露的）

1. **`role` 静默丢失**：`CRUDUser.create` 是显式构造 `User(...)` 并逐个列字段的，漏了
   `role`，于是"创建 viewer"实际得到 admin——这比没有该功能更危险。已补透传。
2. **`panel.py` 缺 `get_current_user` 导入**：把 `ensure_superuser` 换成 `get_current_user`
   后模块级 `NameError`，面板启动即崩溃（502）。**`py_compile` 通过但没执行模块级代码**，
   必须真跑一次 `from app.main import app`——这是本轮最直接的方法论教训。
3. **schema 取值未受限**：`role` 原为自由字符串，`role="root"` 也能写进去。改为
   `Literal["admin","viewer"]`，非法值返回 422。

三个刻意的边界：令牌无效时仍返回 401（不伪装成 403）；数据库异常时放行（宁可漏拦不误封）；
登录接口本身是 POST，必须放行。
## [1.3.9] — 2026-10-18

文档与发布：英文 README、明确未实现清单、修正过时的托管白名单，并打第一个 tag。

### 新增

- `README.en.md`：英文概览（安装、可管理项、设计要点、容器要求、已验证环境、第三方致谢）。
- 「明确未实现的功能」小节：写清 **重建 CA** 与 **轮换 tls-crypt** 不提供界面操作，以及
  为什么不做（影响面覆盖全部客户端且不可逆，做成按钮的收益小于误操作风险），并给出替代路径
  （提前换签用证书到期提醒；改坏了用配置历史回滚）。
- 首个发布 tag。

### 修复 — 文档落后于实现

托管白名单仍写着旧版的 19 条指令。实际已增至 **24 条**（`reneg-sec` / `tls-version-min` /
`verb` / `mute` / `inactive`），`push` 允许前缀也从 6 种增至 **7 种**（含 `block-outside-dns`）。
README 已同步——这类"文档跑在实现后面"的偏差会让用户以为某些设置不能改。
## [1.3.8] — 2026-10-18

Docker 部署形态。

### 新增

- `docker/Dockerfile`：Debian 12 + OpenVPN + 面板 + 容器内 nginx 的一体化镜像。
- `docker/entrypoint.sh`：首次启动初始化 PKI 与 `server.conf`，然后同时管理 openvpn / nginx / 面板
  三个进程（容器里 entrypoint 就是服务管理器），任一关键进程退出则整容器退出并等待重启。
- `docker/nginx.conf`：容器内只做静态与反代（`/ovpnmanager/`、`/api/`、`/share/`）。
- `docker-compose.yml`：`NET_ADMIN` + `/dev/net/tun` + 三个命名卷 + healthcheck。
- `.dockerignore`。

### 关键设计

容器里**没有 systemd**，所以"改配置 / 重启服务"不能走宿主机那套 socket 激活。
镜像**复刻了同一套权限模型**而不是改代码：面板仍以非 root 用户 `ovpnpanel` 运行，
只通过 `/etc/sudoers.d/ovpnmanager` 里几条固定规则调用同一批助手脚本（助手不接受参数），
`helper_client` 的 sudo 兜底通道因此自然可用。**面板代码零改动。**

容器内不适用：面板的「在线升级」「服务重启」（依赖 systemd）。升级容器用
`docker compose pull && up -d`。

### 状态说明（如实记录）

本轮完成了文件与 `entrypoint.sh` 语法校验，**尚未在真机跑通一次完整
`docker compose up --build`**。README 里对此有明确标注，下一轮补齐真机构建与容器内自检证据。
## [1.3.7] — 2026-10-18

面板内一键诊断。

### 新增

- `GET /api/health/selfcheck`（需登录）：11 项只读检查，每项带状态、结果与处置建议。
- 「面板设置」页新增「一键诊断」卡片：一键运行 + 汇总标签 + 明细表。

检查项：OpenVPN / 面板 / nginx 服务、特权助手通道（apply.socket）、IP 转发、托管配置块、
管理接口、OpenVPN 端口监听、ta.key 可读性、证书到期、磁盘空间。

### 设计决定

1. **不复用 `/diagnostics`**：那个被 nginx 限制成 `allow 127.0.0.1`（留给本机监控），
   浏览器调不到。新开一个需登录的接口，语义和可达性都对。
2. **每项独立容错**：单项探不到只标 `unknown`，不让整个自检 500——最需要诊断时它不能先挂。
   本地（无 systemd）实测就是 2 ok / 2 fail / 7 unknown，接口本身仍返回 200。
3. **不给建议就不算诊断**：每项都带一句"该怎么办"。

### 修复

- UDP 端口探测：Linux 上绑定已占用端口抛的是 `EADDRINUSE(98)`，`<1024` 抛 `EACCES`，
  而代码只把 `PermissionError` 当成"在监听"，于是线上唯一一项显示 `unknown`。
  改为按 errno 判断后真机 11/11 全 ok。

真机验证（110.42.102.239）：未登录 401；登录后 11 项全 ok，摘要 ok；前端入口包含
`health/selfcheck`；面板首页 200；两个服务全程 active。
## [1.3.6] — 2026-10-17

配置历史、差异与一键回滚。

### 新增

- `GET /api/settings/history`：列出 `server.conf.bak.*`（名称、大小、UTC 时间）。
- `GET /api/settings/history/{name}`：返回备份内容与**统一差异**（unified diff，超长截断）。
- `POST /api/settings/history/{name}/restore?confirm=RESTORE`：用备份回滚。
- 「服务端设置」页面底部新增「配置历史」卡片：表格 + 差异弹窗 + 回滚（需手输 `RESTORE`）。

### 设计决定

1. **回滚复用已有落地通道**，而不是新写一个特权脚本：把备份内容原样写成候选文件，
   交给 `ACTION=server-conf` 助手去做备份/原子替换/重启/就绪检查/失败自动还原。
   这条路走过真机验证，多一条入口就多一份风险。
2. **备份必须先通过候选校验**。宁可拒绝（例如早期版本里已被移除的指令），也不要把
   一份"当年能跑、现在可能起不来"的配置写回去。
3. **回滚前先备份当前配置**，所以回滚错了还能再回滚回来。

### 安全

- 文件名严格匹配 `server.conf.bak.<8位日期>-<6位时间>`；只接受 `/etc/openvpn` 下的普通文件，
  拒绝符号链接、超 1 MiB 的文件与路径穿越。
- 必须显式 `confirm=RESTORE`，否则 400。

真机验证（110.42.102.239）：列表 14 份；差异接口对"与当前一致"的备份返回 `identical=true`
且 diff 为 0 行；`../../etc/passwd` 被 404、`server.conf` 与 `x.bak.1` 被 400；缺 confirm 与
`confirm=YES` 均 400；**实际回滚**一份内容相同的备份返回 `ok:true`、新生成一份备份（15 份）、
OpenVPN 与面板全程 active；前端入口包含 `settings/history`。
## [1.3.5] — 2026-10-17

证书到期展示与提醒。

### 新增

- `GET /api/dashboard/certificate-expiry?days=30`：即将到期 / 已过期未吊销的证书清单，
  含 `days_left` 与 `expired`。
- Dashboard 新增「证书到期」卡片：告警条 + 列表（客户端、到期时间、剩余天数标签），
  7 天内标黄、已过期标红。
- `alerts.count` 不再写死 0 —— 现在等于"30 天内到期/已过期且未吊销"的证书数。
  此前这个字段是占位值，等于把最重要的运维提醒藏了起来。

### 修复 — 证书统计的三个口径错误（真机验证时发现）

1. **`valid` 包含了已过期的证书**：只判 `status == 'valid'`，而状态不会因为到期自动翻转，
   于是同一张证书既算 `valid` 又算 `expired`。
2. **`expired` 包含了已吊销的证书**：一张吊销后又过期的证书同时计入 `revoked` 和 `expired`。
   实测 5 张证书算出 6 个分项。
3. **`days_left` 向下取整**：`timedelta.days` 对负值也是向下取整，3 天前过期会算出 `-4`，
   界面显示成「已过期 4 天」。改用 `total_seconds()/86400` 后 `int()` 向零截断。

现在口径统一为 `total = valid + revoked + expired`，`valid`（有效且未过期）与
`expired`（未吊销且已过期）互不重叠。真机按四种状态逐一验证该不变量：
0/0/0/0 → 1/1/0/0 → 1/0/0/1 → 1/0/1/0，每次分项和都等于 total。
## [1.3.4] — 2026-10-17

流量历史落库 + Dashboard 图表。

### 新增

- `traffic_samples` 表与后台采样线程：每 60 秒把管理接口的累计字节数落库，
  默认保留 30 天（`TRAFFIC_SAMPLE_SECONDS` / `TRAFFIC_RETENTION_DAYS` 可覆盖）。
- `GET /api/traffic/overview?hours=24`：时间序列 + Top 客户端 + 上下行总量。
- Dashboard 新增「流量」卡片：手写 SVG 折线（下行/上行）+ 客户端排行 + 6h/24h/7d 切换。

### 三个实现要点

1. **存累计值、算增量**。管理接口给的是**会话累计**字节数，重连即归零。如果直接存
   增量，重连那一刻会算出负数；存累计值则既能回答"当前速率"也能回答"历史总量"。
   聚合时对 `cur < prev` 专门处理：视为从 0 重新开始。
2. **窗口内第一条只作基线**，不计入增量——否则每次查询都会把"从前的累计值"算成流量。
3. **不引图表库**。只有一条上下行曲线，手写 SVG `path` 足够；引入 ECharts 会让
   前端包体大一个数量级。

### 修复

- `app/db/base.py`（模型注册表）**漏了 `ShareLink`**：上一轮的 `share_links` 表能建出来
  只是靠导入顺序侥幸。已补齐 `ShareLink` 与 `TrafficSample`——否则换台机器或调整导入
  顺序就会少一张表。

真机验证（110.42.102.239）：表已创建；空库返回 0 点 + 友好提示；写入 10 行合成样本后
返回 6 个点、总量 5600/2800、Top 正确、在线数 1→2；随后删除合成数据（剩余 0 行，
不往用户库里留假数据）；前端入口包含 `/traffic/overview`；面板与 OpenVPN 全程 active。
## [1.3.3] — 2026-10-17

客户端分发：一次性下载链接 + 二维码。

### 新增

- `POST /api/clients/{id}/share`：为某个客户端生成一次性下载链接，同时返回二维码。
- `GET /share/{token}`：**无需登录**的下载入口，一次性、限时（默认 60 分钟，可调）。
- 客户端列表每行（以及手机卡片）新增「分享」按钮，弹窗展示二维码、链接和过期时间，
  可一键复制。
- nginx 新增公开的 `location /share/`，**故意不继承**面板的 allow/deny。

### 三个设计决定（都踩过坑）

1. **二维码编码链接，不是配置文件**。内联证书/密钥的 `.ovpn` 约 8 KB，二维码字节
   模式容量只有 2–3 KB，物理上装不下。
2. **下载入口必须公开**。同事拿到链接时还没连上 VPN，如果 `/share/` 也套上面板的
   allow/deny，链接永远打不开（鸡生蛋）。安全边界改为令牌：一次性、限时、库里只存
   sha256，并发兑换用条件更新保证只有一个请求能拿到。
3. **二维码用 SVG，不用 PNG**。PNG 走 `qrcode.image.pure`，需要额外依赖 `pypng`，
   缺了会**静默失败**（实测二维码字段直接为空，链接本身却正常，很容易漏掉）。

### 修复

- 链接里缺端口：nginx 传的 `Host` 是 `$host`，**不含端口**，拼出来是
  `http://ip/share/xxx`，对方点开必然连不上（实测 000）。改为从
  `/etc/ovpn-oneclick/panel.state` 读真实端口补上。

真机验证（110.42.102.239）：生成链接 -> 二维码 11.5 KB SVG -> **公网匿名下载
HTTP 200 / 8377 字节 / `application/x-openvpn-profile`** -> 二次下载 `410`
-> 数据库只存 `ba4fb6ac…` 形式的哈希且 `used_at` 已置位 -> 伪造 token 返回 `410`
-> 三个服务全程 active。
## [1.3.2] — 2026-10-17

补齐第 6 个托管参数，`block-outside-dns`（Windows 客户端 DNS 泄漏）。

### 新增

- **`push "block-outside-dns"`**：面板「服务端设置」里一个开关。Windows 客户端默认会
  绕过隧道、直接用本地网卡的 DNS，内网域名因此解析不到；开启后把这些查询也拉进隧道。
  其他系统会忽略这条 push。
- 它是 **push 型**指令（不是标量），因此走的是另一条链路：助手 `PUSH_ALLOW`、
  面板 `PUSH_ALLOW_PREFIXES`、`parse_push_directives` 三处都要同步；校验也换成
  "无参数指令，多一个词就拒绝"，避免拼错被 OpenVPN 静默忽略。
- 服务端可编辑项 28 → 29，push 允许前缀 6 → 7。

### 修复 — 测试用例的前提失效

`J29b`（"运维自己写的 push 不被接管"）原来拿 `push "block-outside-dns"` 当
"面板表达不了"的例子。它现在被面板托管了，前提不再成立。改用一个首词确实不受管的
形态 `push "route-gateway 10.8.44.1"`。

中间还踩了一次：先改成 `push "dhcp-option WINS 10.0.0.1"`，结果它被 retire 了——
因为 `dhcp-option` 属于受管前缀，`_retire_managed_lines` 会把整条注释退役，
而不是原样保留。这也说明"面板表达不了"的判据是**首词是否在受管前缀里**，
不是"是否被识别成某种形态"。

真机验证（110.42.102.239）：开启后 `server.conf` 出现 `push "block-outside-dns"`、
读回 `true`、`{"block_outside_dns":"yes"}` 与 `1` 均被 400 拒绝、关闭后残留 0 行、
OpenVPN 全程 active、导出的 `client.ovpn` 不含该行（它是服务端 push，本就不该进客户端）。
## [1.3.1] — 2026-10-17

补齐 5 个此前只能 SSH 改的 OpenVPN 参数，并建立 CI。

### 新增 — 服务端托管参数

服务端可编辑项从 23 增至 28（托管指令从 19 增至 24）：

| 面板字段 | 写入指令 | 取值范围 |
| --- | --- | --- |
| `reneg_sec` | `reneg-sec` | 0–604800 秒（0 = 禁用重协商） |
| `tls_version_min` | `tls-version-min` | 1.0 / 1.1 / 1.2 / 1.3 |
| `verb` | `verb` | 0–11 |
| `mute` | `mute` | 0–10000 |
| `inactive` | `inactive` | 0–86400 秒（0 = 禁用空闲断开） |

这五项都是**可选**：不设置（null）就不写进 `server.conf`，用 OpenVPN 默认值。
注意 0 在这几个参数里有真实含义，因此实现上把"未设置"和"0"严格区分开。

### 新增 — CI

`.github/workflows/ci.yml`，四个互不依赖的作业：语法门禁、bash 断言
（93 + 53 + 159）、跨组件静态检查、面板后端断言（239）。此前 500+ 条断言
全靠人工跑。

### 修复 — CI 首次运行暴露的四个问题

- `templates/apply-dispatch.sh` 其实是 Python（socket 分发器），按扩展名用
  `bash -n` 检查必然失败 → 改为按 **shebang 分派**。
- `static` 作业缺 `sqlalchemy`：`test_block_parity.py` 会真的 import 面板的
  `server_conf_writer` 来证明"面板能渲染安装脚本写的每一种形态"。
- `backend` 作业缺 `httpx`：`starlette.testclient` 依赖它，而运行时的
  `requirements.txt` 里没有 → 新增 `backend/requirements-dev.txt`。
- `G4~G6` 通过替换 `_run` 观察"是否重启"，但服务控制已改为优先走 socket 助手，
  在没有助手的机器上直接抛 `RuntimeError` 把测试打断（表现为 exit 1 却无 `[FAIL]`）
  → 改为替换 `service_action`，只断言"是否请求重启"这个意图。

真机验证（110.42.102.239）：5 项写入后托管块出现对应指令、读回值正确、
越界值全部 400、清空后托管块残留 0 行、OpenVPN 全程 active。
## [1.3.0] — 2026-10-17

把"SSH 只用于救急"这件事做成闭环：除了首次安装，面板里能改完运维需要的一切。这一版新增的功能全部在 110.42.102.239 上真机验证过。

### 新增 — 面板能管的配置

- **19 项服务端托管配置**（此前 11 项）：新增 `data-ciphers`、`auth`、`user`、`group`、`log-append`、`status`。全部写进 `server.conf` 托管块，安装脚本与面板维护同一份，重装/升级不会丢。
- **面板自身设置**：监听端口、绑定域名、放行来源、TLS（含一键自签）、管理员账号口令、会话有效期。
- **网络转发**：`ip_forward` 开关与 NAT 出口网卡。网卡只能从真实接口选，且助手会验证它确有默认路由——改错会让客户端立刻失去出网，而 OpenVPN 服务仍显示 active。
- **一键备份 / 恢复**：备份 PKI、`server.conf`、`.env`、`app.db` 四项（`0600`）；恢复需要手打 `RESTORE`，助手会先做"恢复前备份"、把现有文件整体移走再解包，失败整体还原。
- **在线升级**：面板里检查更新与升级。异步执行、优先用本地安装器副本（实测服务器到 `raw.githubusercontent.com` 不稳定）、进度可轮询。
- **客户端固定 IP 改为可选**：留空由 OpenVPN 从地址池分配；新增时自动预填当前网段里下一个空闲地址，并显示可用范围（此前表单写死 `10.8.0.10`，与默认网段 `10.8.44.0/24` 不符，一提交就被拒）。

### 修复 — 权限与提权链路

- **服务启停全部 500**：面板进程带 `NoNewPrivileges=1`，`sudo` 一律被内核拒绝（`unit` 里写 `NoNewPrivileges=no` 也无效）。新增 `service-control` 助手走 systemd socket 激活，`start/stop/restart` 不再依赖 sudo；`start/restart` 后要求连续 4 秒 `active` 才算成功。
- **`ta.key` 权限导致创建/导出客户端必然失败**：`ta.key` 原为 `0600 root`，而导出 `.ovpn` 必须把它内联进 `<tls-crypt>`，面板以 `ovpnpanel` 运行 → 读不到。改为 `root:ovpnpanel 0640`（只给面板读）。安装脚本同一处早已给 easy-rsa 目录、CCD、CRL 做了 `chown`，唯独漏了它。
- **`data-ciphers` / `auth` 能写但读不回**：它们在 `EDITABLE_KEYS` 里，却只出现在 `readonly_settings()` 的返回中，导致面板这两个字段永远显示空值、差量保存判定失真。
- **PKI 索引权限失效时给出可读原因**：若有人以 root 直接跑过 `easyrsa`，`pki/index.txt` 属主会变成 root，面板随即无法签发证书。现在预检并直接说明"是权限问题 + 怎么修"，不再把几百行 openssl 输出抛给用户。
- **重启面板原本同步执行**：请求自己的连接被切断，客户端只拿到 502，看起来像"重启失败"。改为先返回、3 秒后再重启。

### 新增 — 性能

- 安装时写入 `/etc/sysctl.d/99-ovpn-oneclick.conf`：socket 缓冲上限从默认 208 KiB 抬到 4 MiB（原来会**静默截断** `sndbuf`/`rcvbuf` 的 384 KiB 配置，并压住高延迟客户端），内核提供 `tcp_bbr` 时启用 BBR + fq。
- 去掉 `fast-io`：OpenVPN 2.5+ 已是空操作，留着只会误导。

### 变更 — 安全模型

- 所有提权动作统一走 **systemd socket 激活**的 root 助手（`apply.socket` → `apply@.service`），`sudo` 仅作没有 socket 时的回退。原因是 `NoNewPrivileges` 让 sudo 完全不可用。
- `apply@.service` 去掉了 `PrivateTmp=yes`：它会让该服务的 `/tmp` 与 `/var/tmp` 成为私有 tmpfs，而在线升级要"派生一个脱离会话的后台进程"——服务一退出私有 tmp 就消失，后台进程随即在 `mktemp` 上失败。

### 已知限制

- 单节点、单 OpenVPN 实例，无 HA；数据面吞吐上限由链路带宽决定（实测约 42 Mbps），CPU 单核 AES-256-GCM 约 1.68 GB/s，加密不是瓶颈。
- 在线升级失败**不会自动降级**，需按备份恢复流程手工回滚。
- 文档里的性能结论来自单台机器的实测，不同机房/线路会有差异。
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
