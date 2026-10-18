import api from "./http";

// 用户管理：仅超管可列举/新增/改/删；改自己的口令走 auth.changePassword
export const listUsers = () => api.get("/users/");
export const createUser = (payload) => api.post("/users/", payload);
export const updateUser = (id, payload) => api.patch(`/users/${id}`, payload);
export const deleteUser = (id) => api.delete(`/users/${id}`);
export const changePassword = (payload) => api.post("/auth/change-password", payload);
