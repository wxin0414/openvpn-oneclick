<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">用户管理</h1>
        <p class="page-header__subtitle">
          管理员拥有全部权限；只读账号能看所有页面，但任何写操作都会被拒绝（403）
        </p>
      </div>
      <div class="page-header__actions">
        <el-button @click="load" :loading="loading">刷新</el-button>
        <el-button type="primary" @click="openCreate">新增用户</el-button>
      </div>
    </div>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">账号列表</h2>
      </div>
      <el-table :data="users" size="small">
        <el-table-column prop="username" label="用户名" min-width="150" />
        <el-table-column label="角色" width="130">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small">
              {{ row.role === "admin" ? "管理员" : "只读" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? "启用" : "停用" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="190" />
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button size="small" @click="openReset(row)">重置口令</el-button>
            <el-button size="small" :type="row.is_active ? 'warning' : 'success'" @click="toggleActive(row)">
              {{ row.is_active ? "停用" : "启用" }}
            </el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="field-tip" style="margin-top: 8px">
        最后一个启用的管理员不能被停用或删除（后端会拒绝），避免把自己锁在门外。
      </div>
    </section>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">修改我的口令</h2>
      </div>
      <el-form label-width="140px" style="max-width: 520px">
        <el-form-item label="当前口令" required>
          <el-input v-model="pwd.current" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新口令" required>
          <el-input v-model="pwd.next" type="password" show-password autocomplete="new-password" />
          <div class="field-tip">至少 8 位。改完当前登录会失效，需要重新登录。</div>
        </el-form-item>
        <el-form-item label="确认新口令" required>
          <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="pwdSaving" @click="submitPassword">保存并重新登录</el-button>
        </el-form-item>
      </el-form>
    </section>

    <el-dialog v-model="createVisible" title="新增用户" width="min(92vw, 460px)">
      <el-form label-width="100px">
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" placeholder="如 ops" />
        </el-form-item>
        <el-form-item label="口令" required>
          <el-input v-model="form.password" type="password" show-password />
          <div class="field-tip">至少 6 位（后端限制）。</div>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="只读（viewer）" value="viewer" />
            <el-option label="管理员（admin）" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { changePassword, createUser, deleteUser, listUsers, updateUser } from "../api/users";

const loading = ref(false);
const saving = ref(false);
const users = ref([]);
const createVisible = ref(false);
const form = reactive({ username: "", password: "", role: "viewer" });
const pwd = reactive({ current: "", next: "", confirm: "" });
const pwdSaving = ref(false);

async function load() {
  loading.value = true;
  try {
    const { data } = await listUsers();
    users.value = data || [];
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "读取用户失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  form.username = "";
  form.password = "";
  form.role = "viewer";
  createVisible.value = true;
}

async function submitCreate() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning("用户名和口令都不能为空");
    return;
  }
  saving.value = true;
  try {
    await createUser({
      username: form.username.trim(),
      password: form.password,
      role: form.role,
      is_superuser: form.role === "admin",
    });
    ElMessage.success("已创建");
    createVisible.value = false;
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "创建失败");
  } finally {
    saving.value = false;
  }
}

async function toggleActive(row) {
  try {
    await updateUser(row.id, { is_active: !row.is_active });
    ElMessage.success(row.is_active ? "已停用" : "已启用");
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "操作失败");
  }
}

async function openReset(row) {
  const { value } = await ElMessageBox.prompt(`给「${row.username}」设置新口令（至少 6 位）`, "重置口令", {
    inputType: "password",
    confirmButtonText: "保存",
    cancelButtonText: "取消",
    closeOnClickModal: false,
  }).catch(() => ({ value: null }));
  if (!value) return;
  try {
    await updateUser(row.id, { password: value });
    ElMessage.success("口令已重置");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "重置失败");
  }
}

async function remove(row) {
  const ok = await ElMessageBox.confirm(`删除用户「${row.username}」？`, "确认删除", {
    type: "warning",
    confirmButtonText: "删除",
    cancelButtonText: "取消",
  }).catch(() => false);
  if (!ok) return;
  try {
    await deleteUser(row.id);
    ElMessage.success("已删除");
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "删除失败");
  }
}

async function submitPassword() {
  if (!pwd.current || !pwd.next) {
    ElMessage.warning("请填写当前口令与新口令");
    return;
  }
  if (pwd.next !== pwd.confirm) {
    ElMessage.warning("两次输入的新口令不一致");
    return;
  }
  if (pwd.next.length < 8) {
    ElMessage.warning("新口令至少 8 位");
    return;
  }
  pwdSaving.value = true;
  try {
    await changePassword({ current_password: pwd.current, new_password: pwd.next });
    ElMessage.success("口令已修改，请重新登录");
    localStorage.removeItem("token");
    window.location.href = `${import.meta.env.BASE_URL}login`;
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "修改失败（当前口令是否正确？）");
  } finally {
    pwdSaving.value = false;
  }
}

onMounted(load);
</script>
