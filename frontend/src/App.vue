<template>
  <div v-if="isLoginPage" class="login-wrapper">
    <router-view />
  </div>
  <el-container v-else class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">OpenVPN Web</div>
      <el-menu :default-active="activeMenu" class="menu" router>
        <el-menu-item index="/">
          <el-icon><i class="ri-dashboard-line"></i></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/servers">
          <el-icon><i class="ri-server-line"></i></el-icon>
          <span>服务器管理</span>
        </el-menu-item>
        <el-menu-item index="/clients">
          <el-icon><i class="ri-user-3-line"></i></el-icon>
          <span>客户端管理</span>
        </el-menu-item>
        <!-- <el-menu-item index="/openvpn">
          <el-icon><i class="ri-shield-keyhole-line"></i></el-icon>
          <span>OpenVPN 控制</span>
        </el-menu-item> -->
        <el-menu-item index="/logs">
          <el-icon><i class="ri-file-list-3-line"></i></el-icon>
          <span>日志审计</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="title">OpenVPN Web管理系统</div>
        <div class="user-meta">
          <span class="user-name">{{ username || "管理员" }}</span>
          <el-button size="small" text type="danger" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

const activeMenu = computed(() => route.path);
const isLoginPage = computed(() => route.path === "/login");
const username = computed(() => localStorage.getItem("username") || "");

const logout = () => {
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  router.push("/login");
};
</script>

<style scoped>
.layout {
  height: 100vh;
}
.login-wrapper {
  min-height: 100vh;
  background: radial-gradient(circle at 20% 20%, #0ea5e915, transparent 40%),
    radial-gradient(circle at 80% 0%, #8b5cf615, transparent 45%),
    #0b1220;
  display: flex;
  align-items: center;
  justify-content: center;
}
.aside {
  background: #0f172a;
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
}
.brand {
  padding: 18px 16px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #fff;
}
.menu {
  border-right: none;
  flex: 1;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ffffff;
  border-bottom: 1px solid #f1f5f9;
}
.title {
  font-weight: 600;
}
.user-meta {
  color: #64748b;
  font-size: 14px;
}
.main {
  background: #f8fafc;
}
</style>
