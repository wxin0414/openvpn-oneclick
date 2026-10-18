<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">通知设置</h1>
        <p class="page-header__subtitle">
          服务异常、磁盘告警、证书临期时推送到一个 Webhook；推送失败不会影响面板本身
        </p>
      </div>
      <div class="page-header__actions">
        <el-button @click="load">刷新</el-button>
        <el-button :loading="testing" @click="sendTest">发送测试</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">Webhook</h2>
      </div>
      <el-form label-width="160px" style="max-width: 720px">
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
          <span class="field-tip" style="margin-left: 8px">关闭时不会推送任何消息</span>
        </el-form-item>
        <el-form-item label="Webhook 地址">
          <el-input v-model="form.webhook_url" placeholder="https://example.com/hook" />
          <div class="field-tip">
            只接受 http:// 或 https://。推送体是 JSON：
            <code>source / level / title / message / key / at</code>
          </div>
        </el-form-item>
        <el-form-item label="最低推送等级">
          <el-select v-model="form.min_level" style="width: 220px">
            <el-option label="仅 critical（推荐）" value="critical" />
            <el-option label="warning 及以上" value="warning" />
          </el-select>
          <div class="field-tip">warning 会包含"面板 TLS 未开启"这类提示，量大时建议只用 critical</div>
        </el-form-item>
        <el-form-item label="冷却时间（秒）">
          <el-input-number v-model="form.cooldown_seconds" :min="0" :max="86400" :step="60" />
          <div class="field-tip">
            同一条告警在冷却期内只推一次。<strong>该状态在进程内保存，面板重启后会重发一次。</strong>
          </div>
        </el-form-item>
      </el-form>
    </section>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">触发来源</h2>
      </div>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="巡检频率">
          每 {{ 300 }} 秒一轮（服务端 <code>ALERT_CHECK_SECONDS</code> 可调，最少 30 秒）
        </el-descriptions-item>
        <el-descriptions-item label="会推送什么">
          OpenVPN / 面板 / nginx 服务异常、特权助手通道断开、管理接口不可用、磁盘不足、证书临期或已过期
        </el-descriptions-item>
        <el-descriptions-item label="不会推送什么">
          告警恢复（目前不发"已恢复"通知）；同类告警在冷却期内不重复
        </el-descriptions-item>
      </el-descriptions>
    </section>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { getNotify, saveNotify, testNotify } from "../api/notify";

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const form = reactive({
  enabled: false,
  webhook_url: "",
  min_level: "critical",
  cooldown_seconds: 1800,
});

async function load() {
  loading.value = true;
  try {
    const { data } = await getNotify();
    form.enabled = Boolean(data?.enabled);
    form.webhook_url = data?.webhook_url || "";
    form.min_level = data?.min_level || "critical";
    form.cooldown_seconds = Number(data?.cooldown_seconds ?? 1800);
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "读取通知配置失败（需要管理员权限）");
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (form.webhook_url && !/^https?:\/\//i.test(form.webhook_url)) {
    ElMessage.warning("Webhook 必须以 http:// 或 https:// 开头");
    return;
  }
  saving.value = true;
  try {
    await saveNotify({ ...form });
    ElMessage.success("已保存");
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function sendTest() {
  testing.value = true;
  try {
    const { data } = await testNotify();
    if (data?.ok) ElMessage.success(`测试消息已发送（${data.reason}）`);
    else ElMessage.error(`发送失败：${data?.reason || "未知原因"}`);
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "测试发送失败");
  } finally {
    testing.value = false;
  }
}

onMounted(load);
</script>
