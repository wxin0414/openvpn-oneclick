import api from "./http";

export const getDashboardMetrics = () => api.get("/dashboard/metrics");
