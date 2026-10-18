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

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">两步验证（2FA）</h2>
      </div>
      <div class="field-tip" style="margin-bottom: 10px">
        开启后登录需要额外输入手机令牌 App 的 6 位验证码；恢复码用于手机丢失时救急，每张只能用一次。
      </div>

      <el-tag :type="twofa.enabled ? 'success' : 'info'" size="small">
        {{ twofa.enabled ? "已开启" : "未开启" }}
      </el-tag>

      <div v-if="!twofa.enabled" style="margin-top: 12px">
        <el-button :loading="twofaBusy" @click="startTwofa">生成密钥</el-button>
        <div v-if="twofa.secret" style="margin-top: 12px">
          <img v-if="twofa.qr" :src="twofa.qr" alt="2FA 二维码" style="width: 180px; height: 180px" />
          <div class="field-tip" style="margin-top: 6px">
            用 Google Authenticator / 1Password 等扫码；扫不了就手工输入密钥：<code>{{ twofa.secret }}</code>
          </div>
          <el-input
            v-model="twofa.code"
            placeholder="输入 App 上显示的 6 位码"
            maxlength="12"
            style="max-width: 260px; margin-top: 8px"
          />
          <div style="margin-top: 8px">
            <el-button type="primary" :loading="twofaBusy" :disabled="!twofa.code" @click="confirmTwofa">
              确认启用
            </el-button>
          </div>
        </div>
      </div>

      <div v-else style="margin-top: 12px">
        <el-input
          v-model="twofa.code"
          placeholder="输入当前 6 位码以关闭 2FA"
          maxlength="12"
          style="max-width: 260px"
        />
        <div style="margin-top: 8px">
          <el-button type="danger" :loading="twofaBusy" :disabled="!twofa.code" @click="turnOffTwofa">
            关闭 2FA
          </el-button>
        </div>
      </div>

      <div v-if="twofa.recovery.length" style="margin-top: 16px">
        <el-alert type="warning" :closable="false" show-icon title="恢复码只显示这一次，请立刻保存">
          <div style="margin-top: 6px; font-family: monospace; line-height: 1.8">
            <div v-for="code in twofa.recovery" :key="code">{{ code }}</div>
          </div>
        </el-alert>
      </div>
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
import { disableTwofa, enableTwofa, getTwofaStatus, setupTwofa } from "../api/twofa";

const loading = ref(false);
const saving = ref(false);
const users = ref([]);
const createVisible = ref(false);
const form = reactive({ username: "", password: "", role: "viewer" });
const pwd = reactive({ current: "", next: "", confirm: "" });
const pwdSaving = ref(false);

// ---- 两步验证 --------------------------------------------------------------
// 恢复码只在 enable 成功后返回一次，界面用 alert 明确提示"只显示这一次"。
const twofa = reactive({ enabled: false, secret: "", qr: "", code: "", recovery: [] });
const twofaBusy = ref(false);

async function loadTwofa() {
  try {
    const { data } = await getTwofaStatus();
    twofa.enabled = Boolean(data?.enabled);
  } catch {
    /* 无权限或接口异常时保持未开启的展示 */
  }
}

async function startTwofa() {
  twofaBusy.value = true;
  try {
    const { data } = await setupTwofa();
    twofa.secret = data?.secret || "";
    twofa.qr = data?.qr || "";
    twofa.code = "";
    twofa.recovery = [];
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "生成密钥失败");
  } finally {
    twofaBusy.value = false;
  }
}

async function confirmTwofa() {
  twofaBusy.value = true;
  try {
    const { data } = await enableTwofa(twofa.code.trim());
    twofa.enabled = true;
    twofa.recovery = data?.recovery_codes || [];
    twofa.secret = "";
    twofa.qr = "";
    twofa.code = "";
    ElMessage.success("已开启两步验证，请保存下面的恢复码");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "验证码不正确");
  } finally {
    twofaBusy.value = false;
  }
}

async function turnOffTwofa() {
  twofaBusy.value = true;
  try {
    await disableTwofa(twofa.code.trim());
    twofa.enabled = false;
    twofa.code = "";
    twofa.recovery = [];
    ElMessage.success("已关闭两步验证");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "关闭失败（需要当前有效验证码）");
  } finally {
    twofaBusy.value = false;
  }
}

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

onMounted(() => { load(); loadTwofa(); });
</script>
