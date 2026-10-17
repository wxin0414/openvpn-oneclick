<template>
  <div v-if="isLoginPage" class="login-shell">
    <router-view />
  </div>

  <div v-else class="app-shell">
    <!-- Desktop: 220px sidebar. Tablet: 64px icon rail (el-menu collapse). -->
    <aside v-if="!isMobile" class="sidebar">
      <div class="brand">
        <span class="brand-mark">
          <el-icon><i class="ri-shield-keyhole-line"></i></el-icon>
        </span>
        <span v-show="isDesktop" class="brand-text">OpenVPN Web</span>
      </div>
      <el-menu :default-active="activeMenu" router :collapse="!isDesktop" :collapse-transition="false" class="menu">
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><i :class="item.icon"></i></el-icon>
          <template #title>{{ item.label }}</template>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <span v-show="isDesktop" class="version">v{{ version }}</span>
        <el-tooltip content="退出" placement="right" :show-after="200" :disabled="isDesktop">
          <button type="button" class="icon-btn" aria-label="退出登录" @click="logout">
            <el-icon><i class="ri-logout-box-r-line"></i></el-icon>
          </button>
        </el-tooltip>
      </div>
    </aside>

    <!-- Mobile: no persistent sidebar, the same menu lives in a drawer. -->
    <el-drawer v-if="isMobile" v-model="drawerOpen" direction="ltr" size="248px" :with-header="false" class="app-drawer">
      <div class="drawer-inner">
        <div class="drawer-head">
          <span class="brand-mark">
            <el-icon><i class="ri-shield-keyhole-line"></i></el-icon>
          </span>
          <span class="brand-text">OpenVPN Web</span>
          <button type="button" class="icon-btn" aria-label="关闭菜单" @click="drawerOpen = false">
            <el-icon><i class="ri-close-line"></i></el-icon>
          </button>
        </div>
        <el-menu :default-active="activeMenu" router class="menu" @select="drawerOpen = false">
          <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
            <el-icon><i :class="item.icon"></i></el-icon>
            <template #title>{{ item.label }}</template>
          </el-menu-item>
        </el-menu>
        <div class="drawer-foot">
          <span class="drawer-user">{{ username || "管理员" }}</span>
          <el-button text type="danger" @click="logout">退出</el-button>
        </div>
      </div>
    </el-drawer>

    <div class="main">
      <header class="topbar">
        <button v-if="isMobile" type="button" class="icon-btn" aria-label="打开菜单" @click="drawerOpen = true">
          <el-icon><i class="ri-menu-line"></i></el-icon>
        </button>
        <div class="topbar-title">{{ pageTitle }}</div>
        <div class="topbar-actions">
          <el-dropdown trigger="click" placement="bottom-end">
            <button type="button" class="user-trigger" aria-label="用户菜单">
              <span class="user-avatar">{{ userInitial }}</span>
              <span class="user-name">{{ username || "管理员" }}</span>
              <el-icon class="user-caret"><i class="ri-arrow-down-s-line"></i></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="logout">退出</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted} from "vue";
import { useRoute, useRouter } from "vue-router";

import { version as pkgVersion } from "../package.json";

// 版本以**后端**为准：发版只改 VERSION.txt，前端若读 package.json 就会永远停在旧值
// （实测页脚停在 1.4.0）。package.json 的值只作为请求失败时的兜底。
const version = ref(pkgVersion);

// 页脚版本以后端为准（见 backend/app/version.py）。请求失败就保持 package.json 的兜底值，
// 不因为一个版本号把整个界面卡住。
onMounted(async () => {
  try {
    const res = await fetch("/api/health/version", { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      if (data && data.version) version.value = data.version;
    }
  } catch {
    /* 保持兜底值 */
  }
});
import { useBreakpoint } from "./composables/useBreakpoint";

const route = useRoute();
const router = useRouter();
const { isMobile, isDesktop } = useBreakpoint();

const drawerOpen = ref(false);

// Routes and menu entries are unchanged; the OpenVPN control page keeps its
// route but stays out of the menu, exactly as before.
const menuItems = [
  { path: "/", label: "仪表盘", icon: "ri-dashboard-line" },
  { path: "/servers", label: "服务器管理", icon: "ri-server-line" },
  { path: "/clients", label: "客户端管理", icon: "ri-user-3-line" },
  { path: "/settings", label: "服务端设置", icon: "ri-settings-3-line" },
  { path: "/panel", label: "面板设置", icon: "ri-shield-keyhole-line" },
  // { path: "/openvpn", label: "OpenVPN 控制", icon: "ri-shield-keyhole-line" },
  { path: "/logs", label: "日志审计", icon: "ri-file-list-3-line" },
];

const pageTitles = {
  "/": "仪表盘",
  "/servers": "服务器管理",
  "/clients": "客户端管理",
  "/settings": "服务端设置",
  "/logs": "审计日志",
  "/openvpn": "OpenVPN 控制",
};

const activeMenu = computed(() => route.path);
const isLoginPage = computed(() => route.path === "/login");
const username = computed(() => localStorage.getItem("username") || "");
const pageTitle = computed(() => pageTitles[route.path] || "OpenVPN Web管理系统");
const userInitial = computed(() => (username.value || "管理员").trim().slice(0, 1).toUpperCase());

const logout = () => {
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  router.push("/login");
};

// The drawer must not survive a navigation or a resize into the desktop layout.
watch(
  () => route.path,
  () => {
    drawerOpen.value = false;
  },
);
watch(isMobile, (mobile) => {
  if (!mobile) drawerOpen.value = false;
});
</script>

<style scoped>
/* ---------------------------------------------------------------- login --- */
.login-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-6);
  background:
    radial-gradient(900px 480px at 12% -12%, rgba(63, 81, 181, 0.55), transparent 62%),
    radial-gradient(760px 420px at 102% -6%, rgba(26, 35, 126, 0.6), transparent 58%),
    #0d1230;
}

/* ---------------------------------------------------------------- shell --- */
.app-shell {
  display: flex;
  min-height: 100vh;
  background: var(--surface-2);
}

.sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex: 0 0 var(--sidebar-w);
  flex-direction: column;
  width: var(--sidebar-w);
  height: 100vh;
  overflow-y: auto;
  background: linear-gradient(180deg, var(--sidebar-bg) 0%, var(--sidebar-bg-deep) 100%);
  color: var(--sidebar-text);
}

.brand,
.drawer-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--header-h);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--brand-500);
  color: #fff;
  font-size: 17px;
}

.brand-text {
  overflow: hidden;
  color: #fff;
  font-size: var(--fs-14);
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--sidebar-text);
  --el-menu-active-color: #ffffff;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.08);
  --el-menu-hover-text-color: #ffffff;
  --el-menu-item-height: 44px;
  --el-menu-collapse-width: var(--sidebar-w-rail);
  flex: 1;
  padding: var(--space-2);
  overflow-y: auto;
  border-right: none;
}

.menu :deep(.el-menu-item) {
  margin-bottom: 2px;
  border-radius: var(--radius-sm);
}

.menu :deep(.el-menu-item.is-active) {
  background: var(--brand-500);
  color: #fff;
}

.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid rgba(255, 255, 255, 0.07);
}

.version {
  font-size: var(--fs-12);
  color: rgba(255, 255, 255, 0.45);
}

.sidebar .icon-btn:hover,
.drawer-inner .icon-btn:hover {
  background: rgba(255, 255, 255, 0.1);
}

/* --------------------------------------------------------------- drawer --- */
.drawer-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: linear-gradient(180deg, var(--sidebar-bg) 0%, var(--sidebar-bg-deep) 100%);
  color: var(--sidebar-text);
}

.drawer-head .icon-btn {
  margin-left: auto;
}

.drawer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid rgba(255, 255, 255, 0.07);
}

.drawer-user {
  overflow: hidden;
  color: #fff;
  font-size: var(--fs-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ------------------------------------------------------- top bar / main --- */
.main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--header-h);
  padding: 0 var(--space-6);
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(8px);
}

.topbar-title {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-size: var(--fs-16);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 40px;
  padding: 0 var(--space-2) 0 6px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: var(--fs-13);
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition);
}

.user-trigger:hover {
  background: var(--surface-2);
  border-color: var(--border);
}

.user-trigger:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
}

.user-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--fs-12);
  font-weight: 700;
}

.user-name {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-caret {
  color: var(--text-subtle);
  font-size: 14px;
}

.content {
  flex: 1;
  min-width: 0;
  padding: var(--space-6);
}

/* ----------------------------------------------------------- breakpoints ---
   mobile < 768px | tablet 768-1199px (icon rail) | desktop >= 1200px
   -------------------------------------------------------------------------- */
@media (max-width: 1199.98px) {
  .sidebar {
    flex-basis: var(--sidebar-w-rail);
    width: var(--sidebar-w-rail);
  }

  .brand {
    justify-content: center;
    padding: 0;
  }

  .menu {
    padding: var(--space-2) 0;
  }

  .menu :deep(.el-menu-item) {
    margin-bottom: 0;
    border-radius: 0;
  }

  .sidebar-footer {
    flex-direction: column;
    padding: var(--space-3) 0;
  }

  .topbar {
    padding: 0 var(--space-5);
  }

  .content {
    padding: var(--space-5);
  }
}

@media (max-width: 767.98px) {
  .login-shell {
    padding: var(--space-4);
  }

  .topbar {
    gap: var(--space-2);
    height: var(--header-h-sm);
    padding: 0 var(--space-3) 0 var(--space-2);
  }

  .content {
    padding: var(--space-4);
  }

  .user-trigger {
    padding: 0 6px;
  }

  .user-name,
  .user-caret {
    display: none;
  }
}
</style>
