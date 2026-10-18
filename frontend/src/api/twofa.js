import api from "./http";

// 2FA：setup 只生成密钥不启用；enable 用正确码启用并返回一次性恢复码；disable 需当前有效码
export const getTwofaStatus = () => api.get("/2fa/status");
export const setupTwofa = () => api.post("/2fa/setup");
export const enableTwofa = (code) => api.post("/2fa/enable", { code });
export const disableTwofa = (code) => api.post("/2fa/disable", { code });
