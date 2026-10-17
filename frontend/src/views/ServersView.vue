<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">OpenVPN 服务端</h1>
        <p class="page-header__subtitle">单实例管理，支持启停/重启/状态查询</p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" @click="doControl('status')">查询状态</el-button>
        <el-button size="small" type="success" @click="doControl('start')">启动</el-button>
        <el-button size="small" type="warning" @click="doControl('restart')">重启</el-button>
        <el-button size="small" type="danger" @click="doControl('stop')">停止</el-button>
      </div>
    </div>

    <section class="section-card">
      <el-descriptions :column="isMobile ? 1 : 3" border>
        <el-descriptions-item label="名称">{{ server.name || "-" }}</el-descriptions-item>
        <el-descriptions-item label="主机">{{ server.host || "-" }}</el-descriptions-item>
        <el-descriptions-item label="端口">{{ server.port || "-" }}</el-descriptions-item>
        <el-descriptions-item label="协议">{{ server.protocol || "-" }}</el-descriptions-item>
        <el-descriptions-item label="VPN 网关">{{ server.vpn_ip || "-" }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="server.status === 'running' ? 'success' : 'info'">{{ server.status || "unknown" }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <div class="status-panel">
        <div class="section-card__title">OpenVPN 服务状态输出</div>
        <el-input type="textarea" :rows="10" v-model="statusOutput" readonly />
      </div>
    </section>

    <section class="section-card">
      <div class="section-card__head">
        <div>
          <h2 class="section-card__title">server.conf 配置</h2>
          <p class="section-card__desc">编辑后保存生效（保存后建议重启 OpenVPN）</p>
        </div>
        <div class="head-actions">
          <el-button size="small" @click="loadConf">重新加载</el-button>
          <el-button size="small" type="primary" @click="saveConf">保存配置</el-button>
        </div>
      </div>
      <el-input v-model="confContent" type="textarea" :rows="16" resize="vertical" placeholder="server.conf 内容" />
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { controlServer, getServerConf, listServers, updateServerConf } from "../api/servers";
import { useBreakpoint } from "../composables/useBreakpoint";

const { isMobile } = useBreakpoint();

const server = ref({});
const loading = ref(false);
const confContent = ref("");
const lastAction = ref("");
const statusOutput = ref("尚未查询");

const fetchData = async () => {
  loading.value = true;
  try {
    const { data } = await listServers();
    server.value = data?.[0] || {};
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

const loadConf = async () => {
  loading.value = true;
  try {
    const { data } = await getServerConf();
    confContent.value = data.content || "";
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

const saveConf = async () => {
  try {
    await updateServerConf({ content: confContent.value });
    ElMessage.success("配置已保存");
  } catch (e) {
    ElMessage.error(e.message);
  }
};

const formatResult = (data) => {
  if (!data) return "";
  const status = data.status ? `status=${data.status}` : "";
  const resultLine = data.result ? data.result.replace(/\s+/g, " ").trim() : "ok";
  return [data.action, status, resultLine].filter(Boolean).join(" | ");
};

const doControl = async (action) => {
  try {
    const { data } = await controlServer(action);
    lastAction.value = formatResult(data);
    statusOutput.value = data.result || JSON.stringify(data, null, 2);
    ElMessage.success("操作成功");
    await fetchData();
  } catch (e) {
    ElMessage.error(e.message);
  }
};

onMounted(() => {
  fetchData();
  loadConf();
  doControl("status");
});
</script>

<style scoped>
.section-card__desc {
  margin-top: var(--space-1);
  font-size: var(--fs-13);
  color: var(--text-muted);
}

.head-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.head-actions .el-button + .el-button {
  margin-left: 0;
}

.status-panel {
  margin-top: var(--space-4);
}

.status-panel .section-card__title {
  margin-bottom: var(--space-2);
}
</style>
