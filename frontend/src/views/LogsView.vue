<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="panel-header">
          <span>审计日志</span>
          <div class="filters">
            <el-select
              v-model="filterAction"
              placeholder="动作"
              clearable
              filterable
              size="small"
              style="width: 200px"
              @change="handleFilter"
            >
              <el-option
                v-for="(label, key) in actionOptions"
                :key="key"
                :label="label"
                :value="key"
              />
            </el-select>
            <el-date-picker
              v-model="filterRange"
              type="datetimerange"
              value-format="YYYY-MM-DDTHH:mm:ss"
              range-separator="至"
              start-placeholder="开始时间"
              end-placeholder="结束时间"
              size="small"
              clearable
              @change="handleFilter"
            />
            <el-button size="small" type="primary" @click="handleFilter">查询</el-button>
            <el-button size="small" @click="resetFilter">重置</el-button>
          </div>
        </div>
      </template>
      <el-table :data="logs" v-loading="loading" size="small" empty-text="暂无日志">
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column prop="actor" label="操作者" width="140" />
        <el-table-column prop="action" label="动作" width="160">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ formatAction(row.action) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target" label="目标" />
        <el-table-column prop="result" label="结果" width="120">
          <template #default="{ row }">
            <el-tag :type="row.result === 'success' ? 'success' : 'danger'">{{ row.result }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="pageSizes"
          layout="total, sizes, prev, pager, next, jumper"
          :total="total"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { listAuditLogs } from "../api/logs";
import { formatTimeZh } from "../utils/time";

const logs = ref([]);
const loading = ref(false);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const pageSizes = ref([20, 50, 100, 200]);
const filterAction = ref("");
const filterRange = ref([]);
const actionOptions = ref({
  user_login: "用户登录",
  client_login: "客户端上线",
  client_logout: "客户端下线",
  client_disconnect: "强制下线",
  create_client: "创建客户端",
  delete_client: "删除客户端",
  revoke_client_cert: "吊销证书",
});

const fetchLogs = async () => {
  loading.value = true;
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      action: filterAction.value || undefined,
    };
    if (filterRange.value && filterRange.value.length === 2) {
      params.start_time = filterRange.value[0];
      params.end_time = filterRange.value[1];
    }
    const { data } = await listAuditLogs(params);
    logs.value = data.items || [];
    total.value = data.total || 0;
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

const handleSizeChange = () => {
  page.value = 1;
  fetchLogs();
};

const handlePageChange = () => {
  fetchLogs();
};

const handleFilter = () => {
  page.value = 1;
  fetchLogs();
};

const resetFilter = () => {
  filterAction.value = "";
  filterRange.value = [];
  handleFilter();
};

const formatTime = formatTimeZh;

const formatAction = (action) => {
  return actionOptions.value[action] || action;
};

onMounted(fetchLogs);
</script>

<style scoped>
.page {
  padding: 12px;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filters {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
