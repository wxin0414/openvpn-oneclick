import api from "./http";

// otp 走 query 参数：后端的登录表单是 OAuth2 password 形状，加字段会牵连整个调用，
// 所以两步验证码用 ?otp= 传（见后端 auth.py 的说明）。
export const login = (username, password, otp) =>
  api.post(
    otp ? `/auth/login?otp=${encodeURIComponent(otp)}` : "/auth/login",
    new URLSearchParams({
      username,
      password,
    }),
    {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    },
  );