<template>
  <div class="page">
    <div class="toolbar">
      <div class="filters">
        <el-input
          v-model="filterName"
          placeholder="按名称搜索"
          clearable
          size="small"
          style="width: 200px"
          @clear="handleFilter"
          @keyup.enter.native="handleFilter"
        />
        <el-select v-model="filterStatus" placeholder="状态" clearable size="small" style="width: 140px" @change="handleFilter">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
          <el-option label="禁用" value="disabled" />
        </el-select>
        <el-button size="small" type="primary" @click="handleFilter">查询</el-button>
        <el-button size="small" @click="resetFilter">重置</el-button>
      </div>
      <el-button type="primary" @click="openCreate">新增客户端</el-button>
    </div>
    <el-table :data="displayedClients" style="width: 100%" v-loading="loading">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="common_name" label="证书CN" />
      <el-table-column prop="fixed_ip" label="固定IP" width="140" />
      <el-table-column prop="routes" label="路由" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="success" :loading="busyId === row.id" @click="handleExport(row)">下载</el-button>
          <el-button size="small" type="warning" :disabled="row.status !== 'online'" @click="disconnect(row)">
            强制下线
          </el-button>
          <el-button size="small" type="danger" :loading="busyId === row.id" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="pageSizes"
        :page-count="pageCount"
        layout="total, sizes, prev, pager, next, jumper"
        :total="clients.length"
        @size-change="handleSizeChange"
        @current-change="handlePageChange"
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑客户端' : '新增客户端'" width="520px">
      <el-form :model="form" label-width="120px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" :disabled="isEdit" placeholder="如：client-01" @blur="debouncedValidate('name')" />
          <div
            v-if="validateState.nameOk !== null && validateState.nameMessage"
            :class="['field-tip', validateState.nameOk ? 'ok' : 'err']"
          >
            {{ validateState.nameMessage }}
          </div>
        </el-form-item>
        <el-form-item label="证书CN" required>
          <el-input
            v-model="form.common_name"
            :disabled="isEdit"
            placeholder="如：client-01"
            @blur="debouncedValidate('common_name')"
          />
          <div
            v-if="validateState.commonNameOk !== null && validateState.commonNameMessage"
            :class="['field-tip', validateState.commonNameOk ? 'ok' : 'err']"
          >
            {{ validateState.commonNameMessage }}
          </div>
        </el-form-item>
        <el-form-item label="固定IP" required>
          <el-input
            v-model="form.fixed_ip"
            placeholder="10.8.0.x（必须在 server.conf 网段内）"
            @blur="debouncedValidate('fixed_ip')"
          />
          <div
            v-if="validateState.fixedIpOk !== null && validateState.fixedIpMessage"
            :class="['field-tip', validateState.fixedIpOk ? 'ok' : 'err']"
          >
            {{ validateState.fixedIpMessage }}
          </div>
        </el-form-item>
        <el-form-item label="路由(逗号分隔)">
          <el-input v-model="form.routes" placeholder="192.168.1.0/24,10.0.0.0/24" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option label="在线" value="online" />
            <el-option label="离线" value="offline" />
            <el-option label="禁用" value="disabled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">{{ isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createClient,
  deleteClient,
  listClients,
  updateClient,
  exportClientOvpn,
  checkExportedClientOvpn,
  disconnectClient,
  revokeClientCert,
  validateClient,
} from "../api/clients";
import { waitForJob } from "../api/jobs";
import { debounce } from "lodash-es";

const clients = ref([]);
const loading = ref(false);
// Busy state for the whole submit+poll window of the async job operations:
// `submitting` covers the create/edit dialog, `busyId` marks the table row whose
// certificate operation is still running in the background.
const submitting = ref(false);
const busyId = ref(null);
const dialogVisible = ref(false);
const isEdit = ref(false);
const pageSize = ref(10);
const pageSizes = [10, 20, 50, 100];
const currentPage = ref(1);
const form = reactive({
  id: null,
  name: "client-01",
  common_name: "client-01",
  fixed_ip: "10.8.0.10",
  routes: "",
  status: "offline",
});
const validateState = reactive({
  nameOk: null,
  commonNameOk: null,
  fixedIpOk: null,
  nameMessage: "",
  commonNameMessage: "",
  fixedIpMessage: "",
});

const statusType = (row) => {
  if (row.status === "online") return "success";
  if (row.status === "disabled") return "danger";
  return "info";
};

const total = ref(0);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value) || 1));
const displayedClients = computed(() => clients.value);
const filterName = ref("");
const filterStatus = ref("");

const fetchData = async () => {
  loading.value = true;
  try {
    const { data } = await listClients({
      page: currentPage.value,
      page_size: pageSize.value,
      name: filterName.value || undefined,
      status: filterStatus.value || undefined,
    });
    clients.value = data.items;
    total.value = data.total;
    ensurePageInRange();
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

// ---------------------------------------------------------------------------
// Certificate actions are asynchronous on the backend: the request answers
// 202 + job_id immediately and EasyRSA keeps running on a background worker, so
// the result has to be polled (see ../api/jobs.js).
//
// The check is deliberately on the BODY (`job_id`), not on the 202 status alone:
// a response without a job id is a completed synchronous payload - an older
// backend, or the `?wait=true` compatibility shape - and is returned untouched
// so this view keeps working against either.
// ---------------------------------------------------------------------------
const runJob = async (response, { timeoutMs } = {}) => {
  const body = response?.data;
  const jobId = body?.job_id;
  if (!jobId) {
    return body;
  }
  const job = await waitForJob(jobId, timeoutMs ? { timeoutMs } : undefined);
  return job.result;
};

const openCreate = () => {
  Object.assign(form, {
    id: null,
    name: "client-01",
    common_name: "client-01",
    fixed_ip: "10.8.0.10",
    routes: "",
    status: "offline",
    disabled: false,
  });
  isEdit.value = false;
  resetValidateState();
  dialogVisible.value = true;
};

const openEdit = (row) => {
  Object.assign(form, row);
  isEdit.value = true;
  resetValidateState();
  dialogVisible.value = true;
};

const handleCreate = async () => {
  submitting.value = true;
  try {
    if (!form.name || !form.common_name || !form.fixed_ip) {
      throw new Error("名称、证书CN、固定IP均为必填");
    }
    await validateAll(true);
    if (isEdit.value) {
      await updateClient(form.id, {
        fixed_ip: form.fixed_ip,
        routes: form.routes,
        status: form.status,
      });
      ElMessage.success("已更新");
    } else {
      const { value } = await ElMessageBox.prompt("请输入 CA 口令，用于生成客户端证书", "需要口令", {
        inputType: "password",
        inputPlaceholder: "CA 私钥口令（如无可留空）",
        confirmButtonText: "确认创建",
        cancelButtonText: "取消",
        closeOnClickModal: false,
      });
      const passphrase = value?.trim();
      // 提交后证书签发在后台执行，需轮询任务结果；弹窗在轮询期间保持忙碌状态。
      const response = await createClient({
        name: form.name,
        common_name: form.common_name,
        fixed_ip: form.fixed_ip || null,
        routes: form.routes || null,
        status: form.status,
        passphrase: passphrase || null,
      });
      await runJob(response);
      ElMessage.success("已创建");
    }
    dialogVisible.value = false;
    await fetchData();
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error(e.message);
  } finally {
    submitting.value = false;
  }
};

const handleDelete = async (row) => {
  busyId.value = row.id;
  try {
    const { data: check } = await checkExportedClientOvpn(row.id);
    let passphrase = null;
    if (check?.exists) {
      const { value } = await ElMessageBox.prompt(
        "检测到已生成证书和 .ovpn，需吊销后再删除。请输入 CA 口令（如无可留空）",
        "吊销并删除",
        {
          inputType: "password",
          inputPlaceholder: "CA 私钥口令（如无可留空）",
          confirmButtonText: "确认",
          cancelButtonText: "取消",
          closeOnClickModal: false,
        },
      );
      passphrase = value?.trim() || null;
    } else {
      await ElMessageBox.confirm("未生成证书，直接删除数据库记录，确认继续？", "确认删除", {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      });
    }
    // 后台任务里执行"吊销证书 + 删除记录"，这里轮询到结束再刷新列表。
    const response = await deleteClient(row.id, { passphrase });
    await runJob(response);
    ElMessage.success("已删除");
    await fetchData();
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error(e.message);
  } finally {
    busyId.value = null;
  }
};

const disconnect = async (row) => {
  try {
    await disconnectClient(row.id);
    ElMessage.success("已下线");
    await fetchData();
  } catch (e) {
    ElMessage.error(e.message);
  }
};

const handleExport = async (row) => {
  busyId.value = row.id;
  try {
    // 先检查是否已有导出的 ovpn
    const { data: check } = await checkExportedClientOvpn(row.id);
    let ovpnText = check.ovpn;
    if (!check.exists || !ovpnText) {
      const { value } = await ElMessageBox.prompt("请输入 CA 口令，用于签发客户端证书", "需要口令", {
        inputType: "password",
        inputPlaceholder: "CA 私钥口令（如无可留空）",
        confirmButtonText: "生成并下载",
        cancelButtonText: "取消",
        closeOnClickModal: false,
      });
      const passphrase = value?.trim();
      // 后台任务签发证书，轮询到成功后从 job.result 取 ovpn（与旧的同步
      // {ovpn} 响应结构一致）。
      const response = await exportClientOvpn(row.id, { passphrase: passphrase || null });
      const result = await runJob(response);
      ovpnText = result?.ovpn;
    }
    if (!ovpnText) {
      throw new Error("未获取到配置内容");
    }
    const blob = new Blob([ovpnText], { type: "application/x-openvpn-profile" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${row.name || row.common_name || "client"}.ovpn`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    ElMessage.success(check.exists ? "已直接下载现有配置" : "已生成并下载配置");
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error(e.message);
  } finally {
    busyId.value = null;
  }
};

// 吊销证书：POST /clients/{id}/revoke，同样是后台任务。
const handleRevoke = async (row) => {
  busyId.value = row.id;
  try {
    await ElMessageBox.confirm(`确认吊销 ${row.common_name || row.name} 的证书？`, "确认吊销", {
      type: "warning",
      confirmButtonText: "吊销",
      cancelButtonText: "取消",
    });
    const { value } = await ElMessageBox.prompt("请输入 CA 口令（如无可留空）", "需要口令", {
      inputType: "password",
      inputPlaceholder: "CA 私钥口令（如无可留空）",
      confirmButtonText: "确认吊销",
      cancelButtonText: "取消",
      closeOnClickModal: false,
    });
    const passphrase = value?.trim();
    const response = await revokeClientCert(row.id, { passphrase: passphrase || null });
    await runJob(response);
    ElMessage.success("已吊销");
    await fetchData();
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error(e.message);
  } finally {
    busyId.value = null;
  }
};

const ensurePageInRange = () => {
  const maxPage = pageCount.value;
  if (currentPage.value > maxPage) {
    currentPage.value = maxPage;
    // 若因总数减少导致页码回退，重新拉取数据
    fetchData();
  }
};

const handleSizeChange = () => {
  currentPage.value = 1;
  fetchData();
};

const handlePageChange = () => {
  fetchData();
};

const handleFilter = () => {
  currentPage.value = 1;
  fetchData();
};

const resetFilter = () => {
  filterName.value = "";
  filterStatus.value = "";
  handleFilter();
};

const resetValidateState = () => {
  validateState.nameOk = null;
  validateState.commonNameOk = null;
  validateState.fixedIpOk = null;
  validateState.nameMessage = "";
  validateState.commonNameMessage = "";
  validateState.fixedIpMessage = "";
};

const applyValidateResult = (field, ok, message) => {
  if (field === "name") {
    validateState.nameOk = ok;
    validateState.nameMessage = message || "";
  } else if (field === "common_name") {
    validateState.commonNameOk = ok;
    validateState.commonNameMessage = message || "";
  } else if (field === "fixed_ip") {
    validateState.fixedIpOk = ok;
    validateState.fixedIpMessage = message || "";
  }
};

const validateField = async (field, showToast = false) => {
  const params = {};
  if (field === "name" && form.name) params.name = form.name.trim();
  if (field === "common_name" && form.common_name) params.common_name = form.common_name.trim();
  if (field === "fixed_ip" && form.fixed_ip) params.fixed_ip = form.fixed_ip.trim();
  if (Object.keys(params).length === 0) return;
  try {
    const { data } = await validateClient(params);
    if (field === "name") {
      applyValidateResult("name", data.name_ok !== false, data.name_message);
      if (showToast) ElMessage[data.name_ok ? "success" : "warning"](data.name_message || (data.name_ok ? "名称可用" : "名称已存在"));
    } else if (field === "common_name") {
      applyValidateResult("common_name", data.common_name_ok !== false, data.common_name_message);
      if (showToast) ElMessage[data.common_name_ok ? "success" : "warning"](data.common_name_message || (data.common_name_ok ? "证书CN可用" : "证书CN已存在"));
    } else if (field === "fixed_ip") {
      applyValidateResult("fixed_ip", data.fixed_ip_ok !== false, data.fixed_ip_message);
      if (showToast) ElMessage[data.fixed_ip_ok ? "success" : "warning"](data.fixed_ip_message || (data.fixed_ip_ok ? "固定IP可用" : "固定IP不可用"));
    }
  } catch (e) {
    ElMessage.error(e.message);
  }
};

const validateAll = async (showToast = false) => {
  const { data } = await validateClient({
    name: form.name?.trim(),
    common_name: form.common_name?.trim(),
    fixed_ip: form.fixed_ip?.trim(),
  });
  applyValidateResult("name", data.name_ok !== false, data.name_message);
  applyValidateResult("common_name", data.common_name_ok !== false, data.common_name_message);
  applyValidateResult("fixed_ip", data.fixed_ip_ok !== false, data.fixed_ip_message);
  if (showToast) {
    if (data.name_message) ElMessage[data.name_ok ? "success" : "warning"](data.name_message);
    if (data.common_name_message) ElMessage[data.common_name_ok ? "success" : "warning"](data.common_name_message);
    if (data.fixed_ip_message) ElMessage[data.fixed_ip_ok ? "success" : "warning"](data.fixed_ip_message);
  }
  if (data.name_ok === false || data.common_name_ok === false || data.fixed_ip_ok === false) {
    throw new Error(data.name_message || data.common_name_message || data.fixed_ip_message || "校验未通过");
  }
};

const debouncedValidate = debounce((field) => {
  validateField(field, true);
}, 300);

onMounted(fetchData);
</script>

<style scoped>
.page {
  padding: 12px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;
  flex-wrap: wrap;
}
.filters {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.field-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.field-tip.ok {
  color: #67c23a;
}
.field-tip.err {
  color: #f56c6c;
}
</style>
