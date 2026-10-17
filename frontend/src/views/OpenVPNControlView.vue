<template>
  <div class="page">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">OpenVPN 控制</h1>
        <p class="page-header__subtitle">直接下发 systemd 控制指令并查看服务端返回</p>
      </div>
      <div class="page-header__actions">
        <el-button @click="refreshStatus" :loading="loading">刷新状态</el-button>
        <el-button type="success" @click="runAction('start')" :loading="loading">启动</el-button>
        <el-button type="warning" @click="runAction('restart')" :loading="loading">重启</el-button>
        <el-button type="danger" @click="runAction('stop')" :loading="loading">停止</el-button>
      </div>
    </div>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">OpenVPN 服务状态</h2>
      </div>
      <el-input type="textarea" :rows="10" v-model="output" readonly />
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { control, getStatus } from "../api/openvpn";

const loading = ref(false);
const output = ref("尚未查询");

const refreshStatus = async () => {
  loading.value = true;
  try {
    const { data } = await getStatus();
    output.value = data.result || JSON.stringify(data, null, 2);
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

const runAction = async (action) => {
  loading.value = true;
  try {
    const { data } = await control(action);
    output.value = data.result || JSON.stringify(data, null, 2);
    ElMessage.success(`${action} 成功`);
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

onMounted(refreshStatus);
</script>
