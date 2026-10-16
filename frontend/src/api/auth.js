import api from "./http";

export const login = (username, password) =>
  api.post(
    "/auth/login",
    new URLSearchParams({
      username,
      password,
    }),
    {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    },
  );
