<template>
  <div class="page">
    <el-row :gutter="16" class="cards" v-loading="loading">
      <el-col :span="6" v-for="card in cards" :key="card.title">
        <el-card shadow="hover" class="stat-card">
          <div class="card-header">
            <div class="title">{{ card.title }}</div>
            <el-tag :type="card.tagType" effect="dark" size="small">{{ card.tag }}</el-tag>
          </div>
          <div class="value">{{ card.value }}</div>
          <div class="desc">{{ card.desc }}</div>
        </el-card>
      </el-col>
    </el-row>


    <el-row :gutter="16" class="cards">
      <el-col :span="12">
        <el-card shadow="never" class="panel full-height">
          <template #header>
            <div class="panel-header">
              <span>OpenVPN 概览</span>
            </div>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="版本/标题">{{ openvpn.title || "未知" }}</el-descriptions-item>
            <el-descriptions-item label="累计流量">
              入 {{ formatBytes(totalBytesIn) }} / 出 {{ formatBytes(totalBytesOut) }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" class="panel full-height">
          <template #header>
            <div class="panel-header">
              <span>最近连接事件</span>
            </div>
          </template>
          <div v-if="stateEvents.length">
            <el-timeline>
              <el-timeline-item
                v-for="(item, idx) in stateEvents"
                :key="idx"
                :timestamp="formatTimeZh(item.timestamp * 1000)"
                :type="item.state === 'CONNECTED' ? 'success' : 'info'"
              >
                <div class="activity-line">
                  <span class="actor">{{ item.common_name || "N/A" }}</span>
                  <span class="action">{{ item.state }}</span>
                  <span class="target" v-if="item.real_address">({{ item.real_address }})</span>
                  <div class="action" v-if="item.detail">{{ item.detail }}</div>
                </div>
              </el-timeline-item>
            </el-timeline>
          </div>
          <el-empty v-else description="暂无连接事件" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="panel">
      <template #header>
        <div class="panel-header">
          <span>路由表</span>
        </div>
      </template>
      <el-table :data="routingTable" size="small" v-loading="loading" empty-text="暂无路由">
        <el-table-column prop="virtual_address" label="虚拟地址" />
        <el-table-column prop="real_address" label="公网地址" />
        <el-table-column prop="common_name" label="客户端" />
        <el-table-column label="最近活动">
          <template #default="{ row }">
            {{ formatTimeZh(row.last_ref * 1000) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
        <el-card shadow="never" class="panel">
      <template #header>
        <div class="panel-header">
          <span>近期活动</span>
          <el-button size="small" text type="primary" @click="fetchMetrics" :loading="loading">刷新</el-button>
        </div>
      </template>
      <div v-if="recentActivity.length">
        <el-timeline>
          <el-timeline-item
            v-for="(item, idx) in recentActivity"
            :key="idx"
            :timestamp="formatTimeZh(item.created_at)"
            :type="item.result === 'success' ? 'success' : 'danger'"
          >
            <div class="activity-line">
              <span class="actor spaced">{{ item.actor }}</span>
              <span class="action spaced">{{ item.action }}</span>
              <span class="target spaced" v-if="item.target">({{ item.target }})</span>
              <el-tag class="spaced" size="small" :type="item.result === 'success' ? 'success' : 'danger'">
                {{ item.result }}
              </el-tag>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <el-empty v-else description="暂无活动记录" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { getDashboardMetrics } from "../api/dashboard";
import { formatTimeZh } from "../utils/time";

const loading = ref(false);
const metrics = ref({
  online_clients: 0,
  total_clients: 0,
  certificates: { total: 0, valid: 0, revoked: 0, expired: 0 },
  openvpn: { status: "unknown" },
  alerts: { count: 0 },
});
const recentActivity = ref([]);
const routingTable = ref([]);
const stateEvents = ref([]);

const serviceTagType = computed(() => {
  if (metrics.value.openvpn.status === "running") return "success";
  if (metrics.value.openvpn.status === "stopped") return "danger";
  return "warning";
});

const cards = computed(() => [
  {
    title: "在线客户端",
    value: metrics.value.online_clients,
    desc: `共 ${metrics.value.total_clients} 个客户端`,
    tag: "实时",
    tagType: "success",
  },
  {
    title: "证书总数",
    value: metrics.value.certificates.total,
    desc: `有效 ${metrics.value.certificates.valid} | 吊销 ${metrics.value.certificates.revoked} | 过期 ${metrics.value.certificates.expired}`,
    tag: "证书",
    tagType: "info",
  },
  {
    title: "OpenVPN 服务",
    value: metrics.value.openvpn.status === "running" ? "运行中" : metrics.value.openvpn.status,
    desc: "systemd 状态",
    tag: "服务端",
    tagType: serviceTagType.value,
  },
  {
    title: "告警",
    value: metrics.value.alerts.count,
    desc: "最近24小时",
    tag: "告警",
    tagType: metrics.value.alerts.count > 0 ? "danger" : "success",
  },
]);

const fetchMetrics = async () => {
  loading.value = true;
  try {
    const { data } = await getDashboardMetrics();
    metrics.value = data;
    recentActivity.value = data.recent_activity || [];
    routingTable.value = data.openvpn?.routing_table || [];
    stateEvents.value = data.openvpn?.state_events || [];
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

const formatBytes = (bytes) => {
  if (bytes === null || bytes === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let val = Number(bytes);
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i += 1;
  }
  return `${val.toFixed(val >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
};

const formatDuration = (sec) => {
  if (!sec || sec < 0) return "-";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}天 ${h}小时`;
  if (h) return `${h}小时 ${m}分`;
  return `${m}分`;
};

const openvpn = computed(() => metrics.value.openvpn || {});

const uptimeText = computed(() => {
  const uptime = openvpn.value.load_stats?.uptime;
  if (uptime) return formatDuration(uptime);
  if (openvpn.value.time_unix) {
    const seconds = Math.floor(Date.now() / 1000) - openvpn.value.time_unix;
    return formatDuration(seconds);
  }
  return "-";
});

const pickStat = (...keys) => {
  const gs = openvpn.value.global_stats || {};
  for (const key of keys) {
    if (gs[key] !== undefined) return gs[key];
    const lowerKey = Object.keys(gs).find((k) => k.toLowerCase() === key.toLowerCase());
    if (lowerKey) return gs[lowerKey];
  }
  return null;
};

const totalBytesIn = computed(() => {
  if (openvpn.value.load_stats?.bytes_in != null) return openvpn.value.load_stats.bytes_in;
  return pickStat("bytesin", "BYTESIN", "bytes_in");
});

const totalBytesOut = computed(() => {
  if (openvpn.value.load_stats?.bytes_out != null) return openvpn.value.load_stats.bytes_out;
  return pickStat("bytesout", "BYTESOUT", "bytes_out");
});

const maxConn = computed(() => pickStat("MaxConn", "MAXCONN", "maxconn", "max_conn") ?? "-");
const maxBcast = computed(() => pickStat("MaxBcast", "MAXBCAST", "maxbcast", "max_bcast") ?? "-");

onMounted(fetchMetrics);
</script>

<style scoped>
.page {
  padding: 12px;
}
.cards {
  margin-bottom: 16px;
}
.stat-card {
  min-height: 140px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.title {
  font-weight: 600;
}
.value {
  font-size: 28px;
  font-weight: 700;
  margin: 4px 0;
}
.desc {
  color: #94a3b8;
  font-size: 13px;
}
.panel {
  margin-top: 8px;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.activity-line {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
}
</style>
