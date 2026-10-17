import api from "./http";

export const getDashboardMetrics = () => api.get("/dashboard/metrics");
// 证书到期清单（默认未来 30 天，含已过期未吊销）
export const getCertificateExpiry = (days = 30) =>
  api.get("/dashboard/certificate-expiry", { params: { days } });
