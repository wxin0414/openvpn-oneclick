<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import {
  createBackup,
  getPanelSettings,
  listBackups,
  restartPanel,
  restoreBackup,
  updateAdminCredentials,
  updatePanelSettings,
  updateSessionSettings,
} from "../api/panel";

const loading = ref(false);
const saving = ref(false);
const restarting = ref(false);
const currentUrl = ref("");
const appliedAt = ref("");
const lastBackup = ref("");
const statePresent = ref(false);

const form = reactive({
  port: 5698,
  server_name: "_",
  allow_mode: "vpn",
  allow_cidrs_text: "",
  tls_mode: "off",
  tls_cert: "",
  tls_key: "",
});

const sessionMinutes = ref(120);
const admin = reactive({ username: "", password: "", confirm: "" });

const isCustomAllow = computed(() => form.allow_mode === "custom");
const isExistingTls = computed(() => form.tls_mode === "existing");
const portChangedHint = computed(() => form.port !== initial.port);

const initial = reactive({ port: 5698, server_name: "_", allow_mode: "vpn", tls_mode: "off" });

function applyServerState(data) {
  form.port = data.port ?? 5698;
  form.server_name = data.server_name || "_";
  form.allow_mode = data.allow_mode || "vpn";
  form.allow_cidrs_text = (data.allow_cidrs || []).join("\n");
  form.tls_mode = data.tls_mode || "off";
  form.tls_cert = data.tls_cert || "";
  form.tls_key = data.tls_key || "";
  currentUrl.value = data.current_url || "";
  appliedAt.value = data.applied_at || "";
  lastBackup.value = data.last_backup || "";
  statePresent.value = !!data.state_file_present;
  sessionMinutes.value = data.session_minutes ?? 120;
  Object.assign(initial, {
    port: form.port,
    server_name: form.server_name,
    allow_mode: form.allow_mode,
    tls_mode: form.tls_mode,
  });
}

async function load() {
  loading.value = true;
  try {
    const { data } = await getPanelSettings();
    applyServerState(data);
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "读取面板设置失败");
  } finally {
    loading.value = false;
  }
}

function parseCidrs() {
  return form.allow_cidrs_text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildPayload() {
  const payload = {};
  if (form.port !== initial.port) payload.port = Number(form.port);
  if (form.server_name !== initial.server_name) payload.server_name = form.server_name.trim() || "_";
  if (form.allow_mode !== initial.allow_mode || isCustomAllow.value) {
    payload.allow_mode = form.allow_mode;
    if (isCustomAllow.value) payload.allow_cidrs = parseCidrs();
  }
  if (form.tls_mode !== initial.tls_mode || isExistingTls.value) {
    payload.tls_mode = form.tls_mode;
    if (isExistingTls.value) {
      payload.tls_cert = form.tls_cert.trim();
      payload.tls_key = form.tls_key.trim();
    }
  }
  return payload;
}

async function save() {
  if (isCustomAllow.value && parseCidrs().length === 0) {
    ElMessage.warning("自定义放行模式至少要填一条 IP 或网段");
    return;
  }
  if (isExistingTls.value && (!form.tls_cert.trim() || !form.tls_key.trim())) {
    ElMessage.warning("使用已有证书时必须同时填证书与私钥路径");
    return;
  }
  const payload = buildPayload();
  if (Object.keys(payload).length === 0) {
    ElMessage.info("没有需要保存的改动");
    return;
  }

  const willDisconnect =
    payload.port !== undefined || payload.tls_mode !== undefined || payload.server_name !== undefined;
  if (willDisconnect) {
    try {
      await ElMessageBox.confirm(
        "改端口、域名或 TLS 会让当前页面立刻断开（这是正常现象，不是保存失败）。保存后会给出新的访问地址。",
        "确认切换访问入口",
        { confirmButtonText: "仍然保存", cancelButtonText: "取消", type: "warning" },
      );
    } catch {
      return;
    }
  }

  saving.value = true;
  try {
    const { data } = await updatePanelSettings(payload);
    if (data.port_changed) {
      await ElMessageBox.alert(
        `端口已改为 ${data.port}。请用新地址访问：\n${data.new_url}\n\n当前页面即将失效。`,
        "已切换端口",
        { confirmButtonText: "知道了", type: "success" },
      );
    } else {
      ElMessage.success("已保存并生效");
    }
    await load();
  } catch (err) {
    const detail = err?.response?.data?.detail;
    const reason = typeof detail === "string" ? detail : detail?.reason || "保存失败";
    ElMessageBox.alert(reason, "未生效", { type: "error" });
  } finally {
    saving.value = false;
  }
}

async function saveSession() {
  try {
    const { data } = await updateSessionSettings(Number(sessionMinutes.value));
    ElMessage.success(data?.note || "已保存，重启面板后生效");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "保存会话有效期失败");
  }
}

async function saveAdmin() {
  if (admin.password && admin.password !== admin.confirm) {
    ElMessage.warning("两次输入的口令不一致");
    return;
  }
  const payload = {};
  if (admin.username.trim()) payload.username = admin.username.trim();
  if (admin.password) payload.password = admin.password;
  if (Object.keys(payload).length === 0) {
    ElMessage.info("没有需要提交的改动");
    return;
  }
  try {
    const { data } = await updateAdminCredentials(payload);
    ElMessage.success(data?.note || "已更新");
    admin.password = "";
    admin.confirm = "";
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "更新管理员凭据失败");
  }
}

async function doRestart() {
  try {
    await ElMessageBox.confirm("重启会让当前页面短暂中断（约数秒）。", "确认重启面板", {
      confirmButtonText: "重启",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  restarting.value = true;
  try {
    const { data } = await restartPanel();
    ElMessage.success(data?.note || "面板已重启");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "重启失败");
  } finally {
    restarting.value = false;
  }
}

// ---- 备份 ----
const backups = ref([]);
const backingUp = ref(false);

async function loadBackups() {
  try {
    const { data } = await listBackups();
    backups.value = data?.items || [];
  } catch {
    backups.value = [];
  }
}

async function doBackup() {
  backingUp.value = true;
  try {
    const { data } = await createBackup();
    ElMessage.success(`已备份：${data.backup}（${data.files} 个文件）`);
    await loadBackups();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "备份失败");
  } finally {
    backingUp.value = false;
  }
}

async function doRestore(row) {
  // 恢复会覆盖 PKI / 配置 / 数据库，因此要求手打确认词，而不是点一下"确定"
  let input = "";
  try {
    const res = await ElMessageBox.prompt(
      `这会用备份覆盖当前的 CA、server.conf、.env 与数据库，并重启 OpenVPN 与面板。\n恢复前助手会自动再做一次备份，失败会自动还原。\n\n请输入 RESTORE 确认：`,
      "恢复备份",
      {
        confirmButtonText: "确认恢复",
        cancelButtonText: "取消",
        inputPlaceholder: "RESTORE",
        inputValidator: (v) => (v || "").trim().toUpperCase() === "RESTORE" || "请输入 RESTORE",
        type: "warning",
      },
    );
    input = res.value;
  } catch {
    return;
  }
  try {
    const { data } = await restoreBackup(row.backup, (input || "").trim().toUpperCase());
    await ElMessageBox.alert(data?.note || "已恢复，面板即将重启", "恢复完成", { type: "success" });
  } catch (err) {
    const detail = err?.response?.data?.detail;
    ElMessageBox.alert(typeof detail === "string" ? detail : detail?.message || "恢复失败", "未恢复", {
      type: "error",
    });
  }
}

function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

onMounted(() => {
  load();
  loadBackups();
});
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>面板设置</h2>
        <p class="sub">这里的改动会重新渲染 nginx 配置并 reload；失败会自动回滚，不会让你进不去。</p>
      </div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="!statePresent"
      type="info"
      :closable="false"
      show-icon
      title="还没有通过面板改过设置"
      description="当前显示的是安装时的默认值。第一次保存后，改动会记录在 panel.state 里，重装/升级也不会被冲掉。"
    />

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="mt"
      title="TLS 关闭时管理员口令是明文过网的"
      description="如果面板对公网开放，请开启自签证书或提供已有证书，否则登录口令会以明文经过网络。"
    />

    <el-card class="section-card" shadow="never">
      <template #header><b>网络</b></template>
      <el-form label-position="top">
        <div class="grid">
          <el-form-item label="监听端口">
            <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" />
            <div class="hint">对外访问端口（默认 5698）。不能与 OpenVPN 端口相同。</div>
          </el-form-item>
          <el-form-item label="绑定域名">
            <el-input v-model="form.server_name" placeholder="_ 表示任意域名" />
            <div class="hint">nginx 的 server_name；留空保存为 _。</div>
          </el-form-item>
        </div>

        <el-form-item label="放行来源">
          <el-radio-group v-model="form.allow_mode">
            <el-radio-button label="vpn">仅 VPN 网段（推荐）</el-radio-button>
            <el-radio-button label="public">公网（不推荐）</el-radio-button>
            <el-radio-button label="custom">自定义</el-radio-button>
          </el-radio-group>
          <div class="hint">vpn = 只允许 VPN 隧道网段与本机；public = 0.0.0.0/0 与 ::/0。</div>
        </el-form-item>
        <el-form-item v-if="isCustomAllow" label="允许的 IP / 网段（每行一个）">
          <el-input
            v-model="form.allow_cidrs_text"
            type="textarea"
            :rows="4"
            placeholder="203.0.113.7&#10;198.51.100.0/24"
          />
          <div class="hint">最多 32 条。这些行会插在 deny all 之前（nginx first-match）。</div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header><b>安全</b></template>
      <el-form label-position="top">
        <el-form-item label="TLS">
          <el-radio-group v-model="form.tls_mode">
            <el-radio-button label="off">关闭</el-radio-button>
            <el-radio-button label="selfsigned">自签证书</el-radio-button>
            <el-radio-button label="existing">使用已有证书</el-radio-button>
          </el-radio-group>
          <div class="hint">自签证书由助手用 openssl 生成（私钥 0600）；浏览器会提示一次证书警告。</div>
        </el-form-item>
        <template v-if="isExistingTls">
          <el-form-item label="证书路径（fullchain）">
            <el-input v-model="form.tls_cert" placeholder="/etc/ssl/example/fullchain.pem" />
          </el-form-item>
          <el-form-item label="私钥路径">
            <el-input v-model="form.tls_key" placeholder="/etc/ssl/example/privkey.pem" />
          </el-form-item>
        </template>

        <el-form-item label="会话有效期（分钟）">
          <el-input-number v-model="sessionMinutes" :min="10" :max="10080" controls-position="right" />
          <el-button class="ml" @click="saveSession">保存</el-button>
          <div class="hint">写入 .env 的 ACCESS_TOKEN_EXPIRE_MINUTES，重启面板后生效。</div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header><b>管理员账号</b></template>
      <el-form label-position="top">
        <div class="grid">
          <el-form-item label="用户名">
            <el-input v-model="admin.username" placeholder="留空表示不修改" />
          </el-form-item>
          <el-form-item label="新口令">
            <el-input v-model="admin.password" type="password" show-password placeholder="留空表示不修改" />
          </el-form-item>
          <el-form-item label="确认新口令">
            <el-input v-model="admin.confirm" type="password" show-password placeholder="再输一次" />
          </el-form-item>
        </div>
        <div class="hint">口令至少 12 位。改完当前会话仍有效，其它已登录会话到期后失效。</div>
      </el-form>
      <div class="actions">
        <el-button type="primary" @click="saveAdmin">更新账号</el-button>
      </div>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header><b>备份</b></template>
      <div class="actions">
        <el-button type="primary" :loading="backingUp" @click="doBackup">立即备份</el-button>
        <el-button :loading="loading" @click="loadBackups">刷新列表</el-button>
      </div>
      <div class="hint">
        备份 CA 私钥、server.conf、.env 与 app.db 四项；文件在
        /var/backups/ovpn-oneclick 下，权限 0600（含密钥，请勿外传）。
      </div>
      <el-table v-if="backups.length" :data="backups" size="small" style="margin-top: 12px">
        <el-table-column prop="time" label="时间" min-width="170" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ fmtSize(row.size) }}</template>
        </el-table-column>
        <el-table-column prop="files" label="条目" width="80" />
        <el-table-column prop="backup" label="路径" min-width="240" show-overflow-tooltip />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" link @click="doRestore(row)">恢复</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="hint" style="margin-top: 8px">还没有通过面板做过备份。</div>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header><b>当前状态</b></template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="访问地址">{{ currentUrl || "—" }}</el-descriptions-item>
        <el-descriptions-item label="最近应用">{{ appliedAt || "—" }}</el-descriptions-item>
        <el-descriptions-item label="最近备份">{{ lastBackup || "—" }}</el-descriptions-item>
      </el-descriptions>
      <div class="actions">
        <el-button :loading="restarting" @click="doRestart">重启面板</el-button>
        <span class="hint ml">改完会话有效期后需要重启才生效。</span>
      </div>
    </el-card>

    <div class="save-bar">
      <el-button v-if="portChangedHint" type="danger" plain disabled>
        端口已改，保存后需用新地址访问
      </el-button>
      <el-button type="primary" :loading="saving" @click="save">保存并应用</el-button>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.page-header h2 { margin: 0 0 4px; font-size: var(--fs-20, 20px); }
.sub { margin: 0; color: var(--text-muted, #66708a); font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.hint { color: var(--text-muted, #66708a); font-size: 12px; line-height: 1.5; margin-top: 4px; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.ml { margin-left: 8px; }
.mt { margin-top: 8px; }
.save-bar {
  position: sticky; bottom: 0; display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 0; background: var(--surface, #fff);
  border-top: 1px solid var(--border, #e3e7f0);
}
@media (max-width: 767.98px) {
  .grid { grid-template-columns: 1fr; }
  .save-bar { justify-content: stretch; }
  .save-bar .el-button { flex: 1; }
}
</style>
