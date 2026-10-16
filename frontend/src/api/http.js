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
    if (status === 401) {
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
    return Promise.reject(wrapped);
  },
);

export default api;
