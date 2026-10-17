<template>
  <div class="page">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">仪表盘</h1>
        <p class="page-header__subtitle">服务端运行状态、连接事件与近期审计活动</p>
      </div>
      <div class="page-header__actions">
        <el-button size="small" type="primary" @click="fetchMetrics" :loading="loading">刷新</el-button>
      </div>
    </div>

    <section class="stat-grid" v-loading="loading">
      <article v-for="card in cards" :key="card.title" class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__title">{{ card.title }}</span>
          <el-tag :type="card.tagType" effect="light" size="small" round>{{ card.tag }}</el-tag>
        </div>
        <div class="stat-card__value">{{ card.value }}</div>
        <p class="stat-card__desc">{{ card.desc }}</p>
      </article>
    </section>

    <div class="dashboard-grid">
      <section class="section-card">
        <div class="section-card__head">
          <h2 class="section-card__title">OpenVPN 概览</h2>
        </div>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="版本/标题">{{ openvpn.title || "未知" }}</el-descriptions-item>
          <el-descriptions-item label="累计流量">
            入 {{ formatBytes(totalBytesIn) }} / 出 {{ formatBytes(totalBytesOut) }}
          </el-descriptions-item>
        </el-descriptions>
      </section>

      <section class="section-card">
        <div class="section-card__head">
          <h2 class="section-card__title">最近连接事件</h2>
        </div>
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
      </section>
    </div>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">路由表</h2>
      </div>
      <el-table v-if="!isMobile" :data="routingTable" size="small" v-loading="loading" empty-text="暂无路由">
        <el-table-column prop="virtual_address" label="虚拟地址" />
        <el-table-column prop="real_address" label="公网地址" />
        <el-table-column prop="common_name" label="客户端" />
        <el-table-column label="最近活动">
          <template #default="{ row }">
            {{ formatTimeZh(row.last_ref * 1000) }}
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="mobile-list" v-loading="loading">
        <article v-for="(row, idx) in routingTable" :key="idx" class="mobile-item">
          <div class="mobile-item__head">
            <span class="mobile-item__title">{{ row.virtual_address || "-" }}</span>
            <el-tag size="small" type="info" effect="light">{{ row.common_name || "N/A" }}</el-tag>
          </div>
          <dl class="mobile-item__meta">
            <div>
              <dt>公网地址</dt>
              <dd>{{ row.real_address || "-" }}</dd>
            </div>
            <div>
              <dt>最近活动</dt>
              <dd>{{ formatTimeZh(row.last_ref * 1000) }}</dd>
            </div>
          </dl>
        </article>
        <el-empty v-if="!routingTable.length && !loading" description="暂无路由" />
      </div>
    </section>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">近期活动</h2>
      </div>
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
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { getDashboardMetrics } from "../api/dashboard";
import { formatTimeZh } from "../utils/time";
import { useBreakpoint } from "../composables/useBreakpoint";

const { isMobile } = useBreakpoint();

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
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-4);
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.activity-line {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
}

.activity-line .spaced {
  margin-right: 4px;
}

@media (max-width: 767.98px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}
</style>
