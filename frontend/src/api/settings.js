import api from "./http";

// ---------------------------------------------------------------------------
// 服务端设置（server.conf 受管配置块）
//
// 后端路由声明为 /settings/server 与 /settings/reexport-all（无尾斜杠），
// 因此这里不能像 api/clients.js 的集合路由那样补尾斜杠，否则 FastAPI 会对
// 每个请求回 307 Temporary Redirect，白白多一次往返。
//
// PATCH 的 body 是 settings 中任意子集，常用做法是只提交发生变化的字段
// （见 views/SettingsView.vue 的 buildPayload），这样后端只需要最小化改动
// server.conf 里的受管行。
// ---------------------------------------------------------------------------

// 读取当前 server.conf 的受管配置、只读指令，以及文件路径/exists/managed_block_present。
export const getServerSettings = () => api.get("/settings/server");

// 保存修改。payload 为 settings 的子集；响应包含 changed / restart_required /
// restarted / clients_affected / network_changed / backup / message。
export const updateServerSettings = (payload) => api.patch("/settings/server", payload);

// 按当前服务端设置重新导出全部客户端 .ovpn（改端口/协议后必须执行）。
export const reexportAllClients = () => api.post("/settings/reexport-all");
