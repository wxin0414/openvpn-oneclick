<template>
  <div class="page" v-loading="loading">
    <el-card class="server-card" shadow="never">
      <template #header>
        <div class="server-header">
          <div class="meta">
            <div class="title">OpenVPN 服务端</div>
            <div class="desc">单实例管理，支持启停/重启/状态查询</div>
          </div>
          <div class="controls">
            <el-button size="small" @click="doControl('status')">查询状态</el-button>
            <el-button size="small" type="success" @click="doControl('start')">启动</el-button>
            <el-button size="small" type="warning" @click="doControl('restart')">重启</el-button>
            <el-button size="small" type="danger" @click="doControl('stop')">停止</el-button>
          </div>
        </div>
      </template>
      <el-descriptions :column="3" border>
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
        <div class="panel-header">OpenVPN 服务状态输出</div>
        <el-input type="textarea" :rows="10" v-model="statusOutput" readonly />
       
      </div>
    </el-card>

    <el-card class="server-card" shadow="never">
      <template #header>
        <div class="server-header">
          <div class="meta">
            <div class="title">server.conf 配置</div>
            <div class="desc">编辑后保存生效（保存后建议重启 OpenVPN）</div>
          </div>
          <div class="controls">
            <el-button size="small" @click="loadConf">重新加载</el-button>
            <el-button size="small" type="primary" @click="saveConf">保存配置</el-button>
          </div>
        </div>
      </template>
      <el-input
        v-model="confContent"
        type="textarea"
        :rows="16"
        resize="vertical"
        placeholder="server.conf 内容"
      />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { controlServer, getServerConf, listServers, updateServerConf } from "../api/servers";

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
.page {
  padding: 12px;
}
.server-card {
  margin-bottom: 16px;
}
.server-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.meta .title {
  font-weight: 600;
}
.meta .desc {
  color: #94a3b8;
  font-size: 13px;
}
.controls > * + * {
  margin-left: 6px;
}
.last-result {
  margin-top: 10px;
  color: #475569;
  font-size: 13px;
}
.last-result .label {
  margin-right: 6px;
}
.status-panel {
  margin-top: 12px;
}
.panel-header {
  font-weight: 600;
  margin-bottom: 8px;
}
</style>
