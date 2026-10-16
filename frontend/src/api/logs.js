import api from "./http";

export const listAuditLogs = (params = {}) => api.get("/logs/audit", { params });
