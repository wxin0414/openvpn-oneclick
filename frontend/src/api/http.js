import axios from "axios";

const BASE_PATH = "/ovpnmanager";

// Default timeout for ordinary requests.
const DEFAULT_TIMEOUT = 20000;

// Timeout for genuinely slow calls. Certificate generation/revocation/deletion
// still run EasyRSA as a multi-second subprocess on the server. They are now
// submitted as background jobs (submit + poll, both fast), so this is only kept
// for the `?wait=true` compatibility path that blocks until the job is done -
// preserving the old behaviour for any caller that still asks for it.
export const LONG_TIMEOUT = 180000;

const api = axios.create({
  // Same-origin relative default: the SPA and the API are served from one
  // origin, so no CORS preflight is needed at all. The previous hardcoded LAN
  // IP (http://192.168.1.182:8000/api) forced cross-origin requests on every
  // deployment that did not happen to be that host.
  baseURL: import.meta.env.VITE_API_BASE || "/api",
  timeout: DEFAULT_TIMEOUT,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status;
    const message = error?.response?.data?.detail || error.message || "请求失败";
    // 401 分两种：会话过期（要跳登录页）与"口令对了、还缺验证码"（要留在原地弹输入框）。
    // 原来的写法不看响应头，把 2FA 挑战也当成过期 —— 整页跳转会清掉组件状态，
    // 验证码输入框永远设置不上（真机浏览器实测：登录返回 401，界面仍是原表单）。
    const needsOtp = Boolean(error?.response?.headers?.["x-2fa-required"]);
    if (status === 401 && !needsOtp) {
      localStorage.removeItem("token");
      window.location.href = `${BASE_PATH}/login`;
    }
    const wrapped = new Error(message);
    // Carry the HTTP status on the rejected Error: the message alone cannot
    // distinguish "this job id is unknown/expired" (404, terminal for polling)
    // from a transient transport failure that is worth retrying.
    if (status !== undefined) {
      wrapped.status = status;
    }
    // 必须把原始 response 挂回去：组件里普遍用 err.response.data.detail 取后端给的具体原因，
    // LoginView 还用 err.response.headers["x-2fa-required"] 判断要不要弹验证码输入框。
    // 之前这里只包装了 message，response 一丢，上面的判断全部落空——后端说得再清楚，
    // 界面也只会显示通用文案；开了 2FA 的账号更是连验证码框都不出来。
    wrapped.response = error.response;
    wrapped.config = error.config;
    return Promise.reject(wrapped);
  },
);

export default api;
