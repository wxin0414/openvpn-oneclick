import api from "./http";

// 流量历史（时间序列 + Top 客户端）
export const getTrafficOverview = (hours = 24) =>
  api.get("/traffic/overview", { params: { hours } });
