import api from "./http";

// ---------------------------------------------------------------------------
// 面板自身的网络与安全设置
//
// 后端路由声明为 /panel、/panel/admin-credentials、/panel/session、/panel/restart
// （无尾斜杠），所以这里不能补尾斜杠，否则 FastAPI 会对每个请求回 307，白白多一次往返。
//
// PATCH 只提交发生变化的字段即可；响应里会带 new_url / port_changed——
// 改端口或开 TLS 之后当前页面会立刻断开，这是预期行为，前端要据此提示用户去新地址。
// ---------------------------------------------------------------------------

// 读取当前生效值：port / server_name / allow_mode / allow_cidrs / tls_mode /
// tls_cert / tls_key / session_minutes / current_url / applied_at / last_backup
export const getPanelSettings = () => api.get("/panel");

// 保存网络与安全设置。payload 可含：
//   port, server_name, allow_mode('public'|'vpn'|'custom'), allow_cidrs(string[]),
//   tls_mode('off'|'selfsigned'|'existing'), tls_cert, tls_key
export const updatePanelSettings = (payload) => api.patch("/panel", payload);

// 改管理员用户名/口令（只动数据库，即时生效，不需要重启）
export const updateAdminCredentials = (payload) => api.post("/panel/admin-credentials", payload);

// 会话有效期（写入 .env，需要重启面板才生效）
export const getSessionSettings = () => api.get("/panel/session");
export const updateSessionSettings = (sessionMinutes) =>
  api.post("/panel/session", { session_minutes: sessionMinutes });

// 一键备份：PKI + server.conf + .env + app.db -> /var/backups/ovpn-oneclick（只读操作）
export const createBackup = () => api.post("/panel/backup");

// 最近的手动备份列表（读助手写的索引，不含密钥内容）
export const listBackups = () => api.get("/panel/backups");

// 从备份恢复：危险操作，必须带 confirm="RESTORE"（后端强制校验）
export const restoreBackup = (backup, confirm) => api.post("/panel/restore", { backup, confirm });

// 重启面板服务（走受控的 systemctl 包装脚本）
export const restartPanel = () => api.post("/panel/restart");
