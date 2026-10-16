import { createRouter, createWebHistory } from "vue-router";

import DashboardView from "../views/DashboardView.vue";
import ServersView from "../views/ServersView.vue";
import ClientsView from "../views/ClientsView.vue";
import LogsView from "../views/LogsView.vue";
import LoginView from "../views/LoginView.vue";
import OpenVPNControlView from "../views/OpenVPNControlView.vue";

const routes = [
  { path: "/login", name: "login", component: LoginView },
  { path: "/", name: "dashboard", component: DashboardView },
  { path: "/servers", name: "servers", component: ServersView },
  { path: "/clients", name: "clients", component: ClientsView },
  { path: "/logs", name: "logs", component: LogsView },
  { path: "/openvpn", name: "openvpn", component: OpenVPNControlView },
];

const base = "/ovpnmanager/";
const router = createRouter({
  history: createWebHistory(base),
  routes,
});

const publicPaths = ["/login"];
const normalizePath = (path) => (path.startsWith(base) ? path.slice(base.length - 1) || "/" : path);

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem("token");
  const path = normalizePath(to.path);
  if (!token && !publicPaths.includes(path)) {
    return next("/login");
  }
  if (token && path === "/login") {
    return next("/");
  }
  return next();
});

export default router;
