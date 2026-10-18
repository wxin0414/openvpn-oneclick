import api, { LONG_TIMEOUT } from "./http";

// ---------------------------------------------------------------------------
// Collection routes.
// FastAPI declares these as @router.get("/") / @router.post("/"), so the real
// paths are /api/clients/ . Calling "/clients" (no trailing slash) made FastAPI
// answer 307 Temporary Redirect on every list/create call, which - combined with
// the CORS preflight - turned one logical request into 4 network round trips.
// Keep the trailing slash on THESE TWO collection routes only: all of the
// item-scoped and named routes below (/clients/validate, /clients/online,
// /clients/{id}, /clients/{id}/cert, ...) are declared without one, so adding a
// slash there would CREATE new 307s.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// The four certificate actions below are ASYNC by default: they answer
// 202 + job_id immediately and EasyRSA keeps running on a background worker, so
// they use the ordinary default timeout and the caller polls GET /jobs/{id}
// (see ../api/jobs.js waitForJob). The `?wait=true` legacy path of the same
// endpoints blocks until EasyRSA finishes and therefore still needs
// LONG_TIMEOUT; pass `wait: true` to opt into it.
//
// The timeout option is only added when it is actually needed: handing axios
// `{ timeout: undefined }` would override the instance default instead of
// falling back to it.
// ---------------------------------------------------------------------------

const waitOption = (wait) => (wait ? { params: { wait: true }, timeout: LONG_TIMEOUT } : {});

export const listClients = (params = {}) => api.get("/clients/", { params });

export const createClient = (payload, { wait = false } = {}) =>
  api.post("/clients/", payload, waitOption(wait));

export const updateClient = (id, payload) => api.patch(`/clients/${id}`, payload);

// The job revokes the certificate (EasyRSA) and removes the files.
export const deleteClient = (id, payload = {}, { wait = false } = {}) =>
  api.delete(`/clients/${id}`, { data: payload, ...waitOption(wait) });

// Generates the client certificate/OVPN profile via EasyRSA on a background worker.
export const exportClientOvpn = (id, payload = {}, { wait = false } = {}) =>
  api.post(`/clients/${id}/cert`, payload, waitOption(wait));

// Revokes the client certificate via EasyRSA on a background worker.
export const revokeClientCert = (id, payload = {}, { wait = false } = {}) =>
  api.post(`/clients/${id}/revoke`, payload, waitOption(wait));

export const checkExportedClientOvpn = (id) => api.get(`/clients/${id}/cert/exported`);
export const disconnectClient = (id) => api.post(`/clients/${id}/disconnect`);
export const validateClient = (params = {}) => api.get("/clients/validate", { params });

// 地址池信息：网段 / 可用范围 / 推荐的下一个空闲 IP（用于新增时自动预填）
export const getNextIp = () => api.get("/clients/next-ip");

// 一次性下载链接（含二维码）。返回 { url, qr, expires_at, ttl_minutes }
export const createShareLink = (id, ttlMinutes = 60) =>
  api.post(`/clients/${id}/share`, null, { params: { ttl_minutes: ttlMinutes } });

// 真正的禁用：后端会写 CCD 的 disable 行并断开会话
export const setClientDisabled = (id, disabled) => api.patch(`/clients/${id}`, { disabled });

// 导出客户端清单 CSV（可按分组）：blob，接口要 Bearer 令牌
export const exportClientsCsv = (group) =>
  api.get("/clients/export", { params: group ? { group } : {}, responseType: "blob" });

// 按分组打包导出 .ovpn（zip）
export const exportOvpnZip = (group) =>
  api.get("/clients/export-ovpn", { params: group ? { group } : {}, responseType: "blob" });
