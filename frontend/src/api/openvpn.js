import api from "./http";

export const getStatus = () => api.get("/openvpn/status");
export const control = (action) => api.post(`/openvpn/${action}`);
