import api from "./http";

// NOTE: FastAPI declares this collection route as @router.get("/"), so the real
// path is /api/servers/ . Requesting "/servers" makes FastAPI answer 307 on every
// call (plus a CORS preflight), which is why the trailing slash is required here.
export const listServers = () => api.get("/servers/");
export const updateServer = (id, payload) => api.patch(`/servers/${id}`, payload);
export const getServerConf = () => api.get("/servers/config");
export const updateServerConf = (payload) => api.put("/servers/config", payload);
export const controlServer = (action) => api.post(`/servers/control/${action}`);
