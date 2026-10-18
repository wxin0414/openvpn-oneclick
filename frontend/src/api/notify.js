import api from "./http";

// 通知配置：GET/PUT 取存配置，POST /test 立即发一条测试消息
export const getNotify = () => api.get("/notify/");
export const saveNotify = (payload) => api.put("/notify/", payload);
export const testNotify = () => api.post("/notify/test");
