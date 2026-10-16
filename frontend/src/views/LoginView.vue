<template>
  <div class="login-card">
    <div class="logo">OpenVPN Web 管理</div>
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
  width: 360px;
  padding: 28px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #e2e8f0;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}
.logo {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 16px;
  text-align: center;
  color: #fff;
}
.full-btn {
  width: 100%;
  margin-top: 8px;
}
.hint {
  color: #cbd5e1;
  font-size: 12px;
  margin-top: 12px;
  text-align: center;
}
</style>
