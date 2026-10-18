<template>
  <div class="page">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">客户端管理</h1>
        <p class="page-header__subtitle">签发证书、维护固定 IP 与路由，查看连接状态</p>
      </div>
      <div class="page-header__actions">
        <el-button type="primary" @click="openCreate">新增客户端</el-button>
      <el-button @click="downloadsVisible = true">客户端下载</el-button>
      <el-button :loading="exporting" @click="doExportCsv">导出清单</el-button>
      <el-button :loading="exporting" @click="doExportOvpn">导出配置包</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-input
        v-model="filterName"
        class="filter-field"
        placeholder="按名称搜索"
        clearable
        size="small"
        @clear="handleFilter"
        @keyup.enter.native="handleFilter"
      />
      <el-select v-model="filterStatus" class="filter-field--sm" placeholder="状态" clearable size="small" @change="handleFilter">
        <el-option label="在线" value="online" />
        <el-option label="离线" value="offline" />
        <el-option label="禁用" value="disabled" />
      </el-select>
      <el-select v-model="filterGroup" class="filter-field--sm" placeholder="分组" clearable size="small" @change="handleFilter">
        <el-option label="未分组" value="__none__" />
        <el-option v-for="g in groupOptions()" :key="g" :label="g" :value="g" />
      </el-select>
      <el-button size="small" type="primary" @click="handleFilter">查询</el-button>
      <el-button size="small" @click="resetFilter">重置</el-button>
    </div>

    <section class="section-card">
      <el-table v-if="isDesktop" :data="displayedClients" style="width: 100%" v-loading="loading">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="common_name" label="证书CN" />
        <el-table-column prop="fixed_ip" label="固定IP" width="140" />
        <el-table-column prop="routes" label="路由" />
        <el-table-column label="到期" width="120">
          <template #default="{ row }">
            {{ row.expires_at ? String(row.expires_at).slice(0, 10) : "永不过期" }}
          </template>
        </el-table-column>
        <el-table-column label="配额" width="100">
          <template #default="{ row }">{{ row.quota_mb ? row.quota_mb + " MB" : "不限" }}</template>
        </el-table-column>
        <el-table-column label="分组" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.group_name" size="small" type="info">{{ row.group_name }}</el-tag>
            <span v-else class="muted">未分组</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="430">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="success" :loading="busyId === row.id" @click="handleExport(row)">下载</el-button>
            <el-button size="small" @click="openShare(row)">分享</el-button>
            <el-button size="small" :type="row.disabled ? 'success' : 'warning'" @click="toggleDisabled(row)">
              {{ row.disabled ? "启用" : "禁用" }}
            </el-button>
            <el-button size="small" type="warning" :disabled="row.status !== 'online'" @click="disconnect(row)">
              强制下线
            </el-button>
            <el-button size="small" type="danger" :loading="busyId === row.id" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- Phones and tablets get cards: the six-column table (four action
           buttons wide) would force horizontal scrolling below 1200px. -->
      <div v-else class="client-list" v-loading="loading">
        <article v-for="row in displayedClients" :key="row.id" class="mobile-item">
          <div class="mobile-item__head">
            <span class="mobile-item__title">
              <span class="status-dot" :class="`is-${row.status}`" aria-hidden="true"></span>
              {{ row.name }}
            </span>
            <el-tag :type="statusType(row)" size="small" effect="light">{{ row.status }}</el-tag>
          </div>
          <dl class="mobile-item__meta">
            <div>
              <dt>虚拟 IP</dt>
              <dd>{{ row.fixed_ip || "-" }}</dd>
            </div>
            <div>
              <dt>证书CN</dt>
              <dd>{{ row.common_name || "-" }}</dd>
            </div>
            <div>
              <dt>路由</dt>
              <dd>{{ row.routes || "-" }}</dd>
            </div>
          </dl>
          <div class="mobile-item__actions">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="success" :loading="busyId === row.id" @click="handleExport(row)">下载</el-button>
            <el-button size="small" @click="openShare(row)">分享</el-button>
            <el-button size="small" :type="row.disabled ? 'success' : 'warning'" @click="toggleDisabled(row)">
              {{ row.disabled ? "启用" : "禁用" }}
            </el-button>
            <el-dropdown trigger="click" placement="bottom-end" @command="(command) => handleRowCommand(command, row)">
              <button type="button" class="icon-btn" aria-label="更多操作">
                <el-icon><i class="ri-more-2-fill"></i></el-icon>
              </button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="disconnect" :disabled="row.status !== 'online'">强制下线</el-dropdown-item>
                  <el-dropdown-item command="delete" :disabled="busyId === row.id" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </article>
        <el-empty v-if="!displayedClients.length && !loading" description="暂无客户端" />
      </div>

      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="pageSizes"
          :page-count="pageCount"
          :layout="isMobile ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper'"
          :small="isMobile"
          :total="clients.length"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </section>

    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑客户端' : '新增客户端'"
      :width="isMobile ? '92vw' : '520px'"
    >
      <el-form :model="form" :label-width="isMobile ? undefined : '120px'" :label-position="isMobile ? 'top' : 'right'">
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
        <el-form-item label="固定IP（可留空自动分配）">
          <el-input
            v-model="form.fixed_ip"
            :placeholder="ipPool.start ? `${ipPool.start} ~ ${ipPool.end}（留空则自动分配）` : '留空则自动分配'"
            @blur="debouncedValidate('fixed_ip')"
          />
          <div v-if="ipPool.start" class="field-tip">
            当前网段 {{ ipPool.network }}/{{ ipPool.cidr }}（掩码 {{ ipPool.netmask }}），
            可用 {{ ipPool.start }} ~ {{ ipPool.end }}，已占用 {{ ipPool.used.length }} 个
          </div>
          <div
            v-if="validateState.fixedIpOk !== null && validateState.fixedIpMessage"
            :class="['field-tip', validateState.fixedIpOk ? 'ok' : 'err']"
          >
            {{ validateState.fixedIpMessage }}
          </div>
        </el-form-item>
        <el-form-item label="到期时间（留空=永不过期）">
          <el-date-picker
            v-model="form.expires_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空则永不过期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="流量配额 MB（留空=不限）">
          <el-input-number v-model="form.quota_mb" :min="0" :max="10000000" :step="100" />
          <div class="field-tip">按累计用量计算；达到后自动写 CCD 的 disable 并断开连接</div>
        </el-form-item>
        <el-form-item label="分组（可选）">
          <el-input v-model="form.group_name" placeholder="如：团队A / 客户B，留空表示未分组" />
          <div class="field-tip">纯标签，用于按组筛选与导出，不影响连接</div>
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
      <el-dialog v-model="shareVisible" title="一次性下载链接" width="min(92vw, 460px)">
      <div v-if="shareLoading" v-loading="true" style="height: 200px"></div>
      <template v-else-if="shareInfo">
        <div v-if="shareInfo.qr" style="text-align: center; margin-bottom: 12px">
          <img :src="shareInfo.qr" alt="下载二维码" style="width: 200px; height: 200px" />
        </div>
        <el-input :model-value="shareInfo.url" readonly>
          <template #append>
            <el-button @click="copyShareUrl">复制</el-button>
          </template>
        </el-input>
        <div class="field-tip" style="margin-top: 8px">
          手机扫码即可下载。链接只能使用一次，{{ shareInfo.ttl_minutes }} 分钟后过期
          （{{ shareInfo.expires_at }}）；用过后需要重新生成。
        </div>
        <div class="field-tip" style="margin-top: 6px">
          还没装客户端？
          <el-link type="primary" :underline="false" @click="downloadsVisible = true">
            按平台下载 OpenVPN 客户端
          </el-link>
        </div>
      </template>
    </el-dialog>
      <el-dialog v-model="downloadsVisible" title="下载 OpenVPN 客户端" width="min(94vw, 720px)">
      <ClientDownloads />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createClient,
  createShareLink,
  setClientDisabled,
  exportClientsCsv,
  exportOvpnZip,
  getNextIp,
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
import { useBreakpoint } from "../composables/useBreakpoint";

import ClientDownloads from "../components/ClientDownloads.vue";
const { isMobile, isDesktop } = useBreakpoint();

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
  expires_at: "",
  quota_mb: null,
  group_name: "",
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

const filterGroup = ref("");
// 下拉选项从当前页数据去重生成：不额外打接口，也不会因为分组很多而拉爆。
const groupOptions = () =>
  [...new Set((clients.value || []).map((c) => c.group_name).filter(Boolean))].sort();

const fetchData = async () => {
  loading.value = true;
  try {
    const { data } = await listClients({
      page: currentPage.value,
      page_size: pageSize.value,
      name: filterName.value || undefined,
      status: filterStatus.value || undefined,
      group: filterGroup.value || undefined,
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

// 地址池信息：网段 / 可用范围 / 推荐地址。读不到就静默跳过，不打扰用户。
const ipPool = reactive({ network: "", netmask: "", cidr: null, start: "", end: "", suggested: "", used: [] });

const loadIpPool = async () => {
  try {
    const { data } = await getNextIp();
    Object.assign(ipPool, {
      network: data.network || "",
      netmask: data.netmask || "",
      cidr: data.cidr ?? null,
      start: data.start || "",
      end: data.end || "",
      suggested: data.suggested || "",
      used: Array.isArray(data.used) ? data.used : [],
    });
  } catch {
    /* 读不到就不预填 */
  }
};

const openCreate = async () => {
  Object.assign(form, {
    id: null,
    name: "client-01",
    common_name: "client-01",
    fixed_ip: "",
    routes: "",
    status: "offline",
    disabled: false,
  });
  isEdit.value = false;
  resetValidateState();
  dialogVisible.value = true;
  // 拉当前网段与推荐地址：原来这里写死 "10.8.0.10"，而服务端网段可能根本不是
  // 10.8.0.0/24（本机是 10.8.44.0/24），于是用户一提交就被"不在网段内"拦住。
  await loadIpPool();
  if (!form.fixed_ip && ipPool.suggested) {
    form.fixed_ip = ipPool.suggested;
  }
};

const openEdit = (row) => {
  Object.assign(form, row);
  isEdit.value = true;
  resetValidateState();
  dialogVisible.value = true;
};

const exporting = ref(false);

async function doExportOvpn() {
  const { value } = await ElMessageBox.prompt(
    "输入分组名打包该组的 .ovpn；留空则打包全部（只有已导出过配置的客户端会被包含）",
    "导出客户端配置包",
    { inputPlaceholder: "如：团队A（留空=全部）", confirmButtonText: "打包", cancelButtonText: "取消" }
  ).catch(() => ({ value: null }));
  if (value === null) return;
  const group = String(value || "").trim();
  exporting.value = true;
  try {
    const res = await exportOvpnZip(group || undefined);
    const name = group ? `ovpn-${group}.zip` : "ovpn-all.zip";
    const url = URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
    ElMessage.success("已打包配置");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "打包失败（该组可能还没有已导出的配置）");
  } finally {
    exporting.value = false;
  }
}

async function doExportCsv() {
  const { value } = await ElMessageBox.prompt(
    "输入分组名导出该组；留空则导出全部客户端",
    "导出客户端清单",
    { inputPlaceholder: "如：团队A（留空=全部）", confirmButtonText: "导出", cancelButtonText: "取消" }
  ).catch(() => ({ value: null }));
  if (value === null) return;
  const group = String(value || "").trim();
  exporting.value = true;
  try {
    const res = await exportClientsCsv(group || undefined);
    const name = group ? `clients-${group}.csv` : "clients-all.csv";
    const url = URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
    ElMessage.success("已导出清单");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "导出失败");
  } finally {
    exporting.value = false;
  }
}

async function toggleDisabled(row) {
  const next = !row.disabled;
  const tip = next
    ? "禁用会写入 CCD 的 disable 行并立即断开该客户端，确认继续？"
    : "重新启用后该客户端可以再次连接，确认继续？";
  const ok = await ElMessageBox.confirm(tip, next ? "禁用客户端" : "启用客户端", {
    type: "warning",
    confirmButtonText: "确认",
    cancelButtonText: "取消",
  }).catch(() => false);
  if (!ok) return;
  busyId.value = row.id;
  try {
    await setClientDisabled(row.id, next);
    ElMessage.success(next ? "已禁用（连接会被拒绝）" : "已启用");
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "操作失败");
  } finally {
    busyId.value = null;
  }
}

// ---- 一次性分享链接（二维码编码的是下载链接，不是 8 KB 的配置本身）----
const downloadsVisible = ref(false);
const shareVisible = ref(false);
const shareInfo = ref(null);
const shareLoading = ref(false);

async function openShare(row) {
  shareVisible.value = true;
  shareLoading.value = true;
  shareInfo.value = null;
  try {
    const { data } = await createShareLink(row.id, 60);
    shareInfo.value = data;
  } catch (err) {
    shareVisible.value = false;
    ElMessage.error(err?.response?.data?.detail || "生成链接失败");
  } finally {
    shareLoading.value = false;
  }
}

async function copyShareUrl() {
  const url = shareInfo.value?.url;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    ElMessage.success("链接已复制");
  } catch {
    ElMessage.warning("当前浏览器不允许访问剪贴板，请手动选中复制");
  }
}

const handleCreate = async () => {
  submitting.value = true;
  try {
    if (!form.name || !form.common_name) {
      throw new Error("名称、证书CN为必填");
    }
    await validateAll(true);
    if (isEdit.value) {
      await updateClient(form.id, {
        fixed_ip: form.fixed_ip,
        routes: form.routes,
        status: form.status,
        expires_at: form.expires_at || null,
        quota_mb: form.quota_mb ?? null,
        group_name: form.group_name || null,
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
        expires_at: form.expires_at || null,
        quota_mb: form.quota_mb ?? null,
        group_name: form.group_name || null,
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

// Mobile card menu: the secondary row actions live in an el-dropdown there, so
// its commands are dispatched onto the very same handlers used by the table.
const handleRowCommand = (command, row) => {
  if (command === "disconnect") disconnect(row);
  if (command === "delete") handleDelete(row);
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
  filterGroup.value = "";
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
.client-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-2);
}

.mobile-item__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}

.mobile-item__actions .el-button + .el-button {
  margin-left: 0;
}

.mobile-item__actions .icon-btn {
  margin-left: auto;
  color: var(--text-muted);
}

@media (min-width: 768px) {
  .client-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-3);
  }
}
</style>
