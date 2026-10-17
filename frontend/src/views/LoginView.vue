<template>
  <div class="login-card">
    <div class="logo">
      <span class="login-mark">
        <el-icon><i class="ri-shield-keyhole-line"></i></el-icon>
      </span>
      <span>OpenVPN Web 管理</span>
    </div>
    <el-form :model="form" @keyup.enter="submit" label-position="top">
      <el-form-item label="用户名">
        <el-input v-model="form.username" autocomplete="username" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="form.password" type="password" autocomplete="current-password" show-password />
      </el-form-item>
      <el-button type="primary" :loading="loading" class="full-btn" @click="submit">登录</el-button>
    </el-form>
  </div>
</template>

<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { login } from "../api/auth";

const router = useRouter();
const loading = ref(false);
const form = reactive({
  username: "",
  password: "",
});

const submit = async () => {
  if (!form.username || !form.password) {
    ElMessage.warning("请输入用户名和密码");
    return;
  }
  loading.value = true;
  try {
    const { data } = await login(form.username, form.password);
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("username", form.username);
    if (data.must_change_password) {
      // 后端在仍使用公开默认口令时会返回该标记（见 backend/app/core/config.py
      // 的 insecure_settings）：之前前端完全忽略它，运维就永远看不到提醒。
      ElMessage.warning({
        message: "当前账号仍在使用默认口令，请立即修改（POST /api/auth/change-password，详见 deploy/README.md）",
        duration: 8000,
      });
    } else {
      ElMessage.success("登录成功");
    }
    router.push("/");
  } catch (e) {
    // 登录限速（429）时后端会给出重试秒数，直接展示即可
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
.login-card {
  width: 100%;
  max-width: 420px;
  padding: 32px 28px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 24px 60px rgba(9, 14, 40, 0.32);
}

.logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  margin-bottom: var(--space-6);
  color: var(--text);
  font-size: var(--fs-20);
  font-weight: 700;
}

.login-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--brand-500);
  color: #fff;
  font-size: 17px;
}

.full-btn {
  width: 100%;
  margin-top: var(--space-2);
}

@media (max-width: 767.98px) {
  .login-card {
    padding: 24px 20px;
  }

  .logo {
    font-size: var(--fs-16);
  }
}
</style>
