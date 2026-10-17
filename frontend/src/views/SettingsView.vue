<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div class="page-header__text">
        <h1 class="page-header__title">服务端设置</h1>
        <p class="page-header__subtitle">
          <span class="path-label">配置文件</span>
          <code>{{ configPath || "-" }}</code>
        </p>
      </div>
      <div class="page-header__actions">
        <el-button :loading="loading" :disabled="loading" @click="reload(true)">重新加载</el-button>
        <el-button
          v-if="!isMobile"
          type="primary"
          :loading="saving"
          :disabled="saving || loading"
          @click="handleSave"
        >
          保存并应用
        </el-button>
      </div>
    </div>

    <el-alert v-if="loaded && !exists" type="error" show-icon :closable="false" title="未找到 server.conf">
      <template #default>
        路径 <code>{{ configPath }}</code> 不存在配置文件，请先通过安装脚本部署 OpenVPN 服务端。
      </template>
    </el-alert>

    <!-- 从其他项目迁移过来的 server.conf 没有本安装器写入的受管块 -->
    <el-alert
      v-if="loaded && managedBlockPresent === false"
      type="warning"
      show-icon
      :closable="false"
      title="该 server.conf 不是由本安装器创建"
    >
      <template #default>
        未检测到安装器写入的受管配置块（可能来自其他项目或历史手工配置）。保存时会插入受管块，
        并注释掉与之冲突的现有指令，使面板的配置生效；保存前会先生成 <code>.bak</code> 备份，
        请确认这些改动符合预期。
      </template>
    </el-alert>

    <el-alert
      v-if="loaded && managedBlockPresent === null"
      type="info"
      show-icon
      :closable="false"
      title="无法确认受管配置块状态"
    >
      <template #default>后端未返回 managed_block_present，保存行为以服务端实际处理结果为准。</template>
    </el-alert>

    <!-- 最近一次保存结果：把 message / backup / 影响面留在页面上，便于事后核对 -->
    <section v-if="lastApply" class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">最近一次保存结果</h2>
        <el-tag :type="lastApply.ok === false ? 'danger' : 'success'" size="small">
          {{ lastApply.ok === false ? "失败" : "成功" }}
        </el-tag>
      </div>
      <div class="apply-line" v-if="lastApply.message">{{ lastApply.message }}</div>
      <div class="apply-line">
        <span class="label">已修改：</span>
        <template v-if="lastApply.changed && lastApply.changed.length">
          <el-tag v-for="key in lastApply.changed" :key="key" size="small" class="tag-gap">{{ key }}</el-tag>
        </template>
        <span v-else class="muted">无</span>
      </div>
      <div class="apply-line">
        <span class="label">备份文件：</span>
        <code v-if="lastApply.backup">{{ lastApply.backup }}</code>
        <span v-else class="muted">后端未返回备份路径</span>
      </div>
      <div class="apply-line">
        <el-space wrap>
          <el-tag :type="lastApply.restarted ? 'success' : 'info'" size="small">
            {{ lastApply.restarted ? "服务已重启" : "服务未重启" }}
          </el-tag>
          <el-tag v-if="lastApply.restart_required" type="warning" size="small">需要重启服务后生效</el-tag>
          <el-tag v-if="lastApply.clients_affected" type="danger" size="small">客户端配置受影响</el-tag>
          <el-tag v-if="lastApply.network_changed" type="danger" size="small">网段已变更</el-tag>
        </el-space>
      </div>
      <div class="apply-line" v-if="lastApply.clients_affected">
        <el-button type="warning" :loading="reexporting" :disabled="reexporting" @click="handleReexportAll">
          重新导出全部客户端
        </el-button>
        <span class="muted hint">端口/协议已变更，已签发的 .ovpn 已失效，必须重新导出并重新分发。</span>
      </div>
      <div class="apply-line" v-if="reexportResult">
        <span class="label">重新导出：</span>
        <el-tag type="success" size="small">成功 {{ reexportResult.exported }}</el-tag>
        <el-tag v-if="reexportResult.failed.length" type="danger" size="small" class="tag-gap">
          失败 {{ reexportResult.failed.length }}
        </el-tag>
        <ul v-if="reexportResult.failed.length" class="failed-list">
          <li v-for="item in reexportResult.failed" :key="item.name">{{ item.name }}：{{ item.error }}</li>
        </ul>
      </div>
    </section>

    <el-form
      :model="form"
      :label-width="isMobile ? undefined : '150px'"
      :label-position="isMobile ? 'top' : 'right'"
      :disabled="saving"
      class="settings-form"
    >
      <div class="settings-grid">
        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">基本</h2>
          </div>
          <el-form-item label="监听端口" required>
            <el-input-number v-model="form.port" :min="1" :max="65535" :step="1" controls-position="right" />
            <div v-if="errors.port" class="field-tip err">{{ errors.port }}</div>
            <div v-else class="field-tip">修改端口后所有已签发的客户端 .ovpn 都会失效。</div>
          </el-form-item>
          <el-form-item label="协议" required>
            <el-select v-model="form.proto" class="control-field">
              <el-option v-for="opt in protoOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
            <div v-if="errors.proto" class="field-tip err">{{ errors.proto }}</div>
            <div v-else class="field-tip">修改协议后所有已签发的客户端 .ovpn 都会失效。</div>
          </el-form-item>
        </section>

        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">隧道与路由</h2>
          </div>
          <el-form-item label="代理模式" required>
            <el-select v-model="form.tunnel_mode" class="control-field">
              <el-option v-for="opt in tunnelModeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
            <div class="field-tip">全局代理：客户端所有流量都走 VPN。分流：仅下表列出的目标网段走 VPN。</div>
          </el-form-item>
          <el-form-item label="分流路由">
            <div class="rows">
              <div v-for="(_, index) in form.routes" :key="`route-${index}`" class="row-item">
                <el-input
                  v-model="form.routes[index]"
                  class="row-input"
                  :disabled="form.tunnel_mode !== 'split'"
                  placeholder="192.168.1.0/24"
                />
                <el-button type="danger" text :disabled="form.tunnel_mode !== 'split'" @click="removeRoute(index)">
                  移除
                </el-button>
                <span v-if="errors.routes[index]" class="field-tip err">{{ errors.routes[index] }}</span>
              </div>
              <el-button size="small" :disabled="form.tunnel_mode !== 'split'" @click="addRoute">添加路由</el-button>
            </div>
            <div class="field-tip">
              {{ form.tunnel_mode === "split" ? "CIDR 格式，如 192.168.1.0/24。" : "仅分流模式可编辑。" }}
            </div>
          </el-form-item>
          <el-form-item label="屏蔽 IPv6">
            <el-switch v-model="form.block_ipv6" />
            <div class="field-tip">开启后客户端侧屏蔽 IPv6 流量，避免 IPv6 泄漏绕过隧道。</div>
          </el-form-item>
        </section>

        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">DNS</h2>
          </div>
          <el-form-item label="DNS 服务器">
            <div class="rows">
              <div v-for="(_, index) in form.dns_servers" :key="`dns-${index}`" class="row-item">
                <el-input v-model="form.dns_servers[index]" class="row-input" placeholder="8.8.8.8" />
                <el-button type="danger" text @click="removeDns(index)">移除</el-button>
                <span v-if="errors.dns[index]" class="field-tip err">{{ errors.dns[index] }}</span>
              </div>
              <el-button size="small" @click="addDns">添加 DNS</el-button>
            </div>
            <div class="field-tip">最多 2 个，推送给客户端用于隧道内解析。</div>
          </el-form-item>
        </section>

        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">性能</h2>
          </div>
          <el-form-item label="MSS 修正 (mssfix)" required>
            <el-input-number v-model="form.mssfix" :min="0" :max="65535" :step="1" controls-position="right" />
            <div v-if="errors.mssfix" class="field-tip err">{{ errors.mssfix }}</div>
            <div v-else class="field-tip">0-65535，用于避免分片，常见值 1420。</div>
          </el-form-item>
          <el-form-item label="发送缓冲区 (sndbuf)" required>
            <el-input-number v-model="form.sndbuf" :min="0" :max="16777216" :step="1024" controls-position="right" />
            <div v-if="errors.sndbuf" class="field-tip err">{{ errors.sndbuf }}</div>
            <div v-else class="field-tip">字节，默认 393216。</div>
          </el-form-item>
          <el-form-item label="接收缓冲区 (rcvbuf)" required>
            <el-input-number v-model="form.rcvbuf" :min="0" :max="16777216" :step="1024" controls-position="right" />
            <div v-if="errors.rcvbuf" class="field-tip err">{{ errors.rcvbuf }}</div>
            <div v-else class="field-tip">字节，默认 393216。</div>
          </el-form-item>
          <el-form-item label="keepalive" required>
            <el-input v-model="form.keepalive" class="control-field" placeholder="10 120" />
            <div v-if="errors.keepalive" class="field-tip err">{{ errors.keepalive }}</div>
            <div v-else class="field-tip">两个整数（间隔 超时），如 “10 120”。</div>
          </el-form-item>
        </section>

        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">客户端</h2>
          </div>
          <el-form-item label="客户端互访">
            <el-switch v-model="form.client_to_client" />
            <div class="field-tip">开启后同一 VPN 内的客户端可以互相访问。</div>
          </el-form-item>
          <el-form-item label="限制最大连接数">
            <el-switch v-model="limitMaxClients" />
            <el-input-number
              v-if="limitMaxClients"
              v-model="form.max_clients"
              :min="1"
              :max="65535"
              :step="1"
              controls-position="right"
              class="inline-number"
            />
            <div v-if="errors.max_clients" class="field-tip err">{{ errors.max_clients }}</div>
            <div v-else class="field-tip">关闭时不下发 max-clients（不限制）。</div>
          </el-form-item>
          <el-form-item label="允许同名 CN">
            <el-switch v-model="form.duplicate_cn" />
            <div class="field-tip">开启后允许同一证书 CN 多设备同时连接，建议保持关闭。</div>
          </el-form-item>
        </section>

        <section class="section-card">
          <div class="section-card__head">
            <h2 class="section-card__title">网段</h2>
          </div>
          <el-form-item label="VPN 网段" required>
            <el-input v-model="form.network" class="control-field" placeholder="10.8.44.0" />
            <div v-if="errors.network" class="field-tip err">{{ errors.network }}</div>
          </el-form-item>
          <el-form-item label="子网掩码" required>
            <el-input v-model="form.netmask" class="control-field" placeholder="255.255.255.0" />
            <div v-if="errors.netmask" class="field-tip err">{{ errors.netmask }}</div>
          </el-form-item>
          <el-form-item label="拓扑 (topology)" required>
            <el-select v-model="form.topology" class="control-field">
              <el-option v-for="opt in topologyOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </el-form-item>
          <el-alert type="warning" show-icon :closable="false" class="net-warning">
            <template #default>
              修改网段或掩码会重建 NAT 规则与面板自身的 nginx 放行规则，并重启 OpenVPN 服务；
              已有客户端的固定 IP 可能需要同步调整。
            </template>
          </el-alert>
        </section>
      </div>
    </el-form>

    <section class="section-card">
      <div class="section-card__head">
        <h2 class="section-card__title">只读指令（由面板 / 安装脚本维护）</h2>
      </div>
      <div class="muted readonly-hint">
        以下指令是面板正常工作所必需的，由安装脚本写入并随面板运行自动维护，此处仅作展示，不可在此修改。
      </div>
      <el-descriptions :column="isMobile ? 1 : 2" border size="small">
        <el-descriptions-item label="management（管理接口）">{{ display(readonlyInfo.management) }}</el-descriptions-item>
        <el-descriptions-item label="status-version">{{ display(readonlyInfo.status_version) }}</el-descriptions-item>
        <el-descriptions-item label="crl-verify（吊销列表）">{{ display(readonlyInfo.crl_verify) }}</el-descriptions-item>
        <el-descriptions-item label="client-config-dir（CCD）">{{ display(readonlyInfo.client_config_dir) }}</el-descriptions-item>
        <el-descriptions-item label="TLS 模式">{{ display(readonlyInfo.tls_mode) }}</el-descriptions-item>
        <el-descriptions-item label="数据加密套件">{{ display(readonlyInfo.data_ciphers) }}</el-descriptions-item>
        <el-descriptions-item label="auth（摘要算法）">{{ display(readonlyInfo.auth) }}</el-descriptions-item>
        <el-descriptions-item label="cipher（旧版对称加密）">{{ display(readonlyInfo.cipher) }}</el-descriptions-item>
      </el-descriptions>
    </section>

    <!-- 手机端把主操作固定在视口底部，长表单滚动时也能随时保存 -->
    <div v-if="isMobile" class="save-bar">
      <el-button type="primary" :loading="saving" :disabled="saving || loading" @click="handleSave">
        保存并应用
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getServerSettings, reexportAllClients, updateServerSettings } from "../api/settings";
import { useBreakpoint } from "../composables/useBreakpoint";

const { isMobile } = useBreakpoint();

// ---------------------------------------------------------------------------
// 服务端设置页：编辑 server.conf 的受管配置块。
// 所有写入走 PATCH /settings/server，body 只带发生变化的字段（buildPayload），
// 让后端做最小改动、必要时才重启服务。
// ---------------------------------------------------------------------------

// 受管 settings 的字段清单：既用于把接口返回值套用到表单，也用于计算差量。
const SETTINGS_KEYS = [
  "port",
  "proto",
  "dns_servers",
  "tunnel_mode",
  "routes",
  "block_ipv6",
  "mssfix",
  "sndbuf",
  "rcvbuf",
  "keepalive",
  "client_to_client",
  "max_clients",
  "duplicate_cn",
  "network",
  "netmask",
  "topology",
];

// 下拉选项。接口若返回列表外的值，会以“当前值”占位追加，避免静默改写配置。
const TUNNEL_MODES = [
  { value: "full", label: "全局代理（full）" },
  { value: "split", label: "分流（split）" },
];
const PROTOS = [
  { value: "udp", label: "UDP" },
  { value: "tcp", label: "TCP" },
];
const TOPOLOGIES = [
  { value: "subnet", label: "subnet（推荐）" },
  { value: "net30", label: "net30（兼容旧客户端）" },
  { value: "p2p", label: "p2p" },
];

// 缓冲区上限：OpenVPN 的 sndbuf/rcvbuf 是普通整数，这里只做防呆上限（16 MiB）。
const BUFFER_MAX = 16777216;
const MAX_ROUTES = 64;

const loading = ref(false);
const saving = ref(false);
const reexporting = ref(false);
const loaded = ref(false);

const configPath = ref("");
const exists = ref(true);
// 三态：true=有受管块，false=不是本安装器创建的，null=后端未返回该字段
const managedBlockPresent = ref(null);
const lastApply = ref(null);
const reexportResult = ref(null);

const form = reactive({
  port: 1194,
  proto: "udp",
  dns_servers: [],
  tunnel_mode: "full",
  routes: [],
  block_ipv6: false,
  mssfix: 1420,
  sndbuf: 393216,
  rcvbuf: 393216,
  keepalive: "10 120",
  client_to_client: true,
  max_clients: null,
  duplicate_cn: false,
  network: "",
  netmask: "",
  topology: "subnet",
});

// 只读指令单独存一份，避免模板里对可能为 null 的对象取属性。
const readonlyInfo = reactive({
  management: null,
  status_version: null,
  crl_verify: null,
  client_config_dir: null,
  tls_mode: null,
  data_ciphers: null,
  auth: null,
  cipher: null,
});

// 最近一次从服务端读到的表单快照（JSON），用于计算差量与风险提示。
const original = ref("{}");

const errors = reactive({
  port: "",
  proto: "",
  mssfix: "",
  sndbuf: "",
  rcvbuf: "",
  keepalive: "",
  max_clients: "",
  network: "",
  netmask: "",
  dns: [],
  routes: [],
});

const display = (value) => {
  if (value === null || value === undefined || value === "") return "（未设置）";
  return String(value);
};

const limitMaxClients = computed({
  get: () => form.max_clients !== null && form.max_clients !== undefined,
  set: (enabled) => {
    form.max_clients = enabled ? 50 : null;
  },
});

const withUnknownValue = (options, value) => {
  const list = [...options];
  if (value && !list.some((item) => item.value === value)) {
    list.push({ value, label: `${value}（当前值）` });
  }
  return list;
};
const protoOptions = computed(() => withUnknownValue(PROTOS, form.proto));
const tunnelModeOptions = computed(() => withUnknownValue(TUNNEL_MODES, form.tunnel_mode));
const topologyOptions = computed(() => withUnknownValue(TOPOLOGIES, form.topology));

// ---------------------------------------------------------------------------
// 客户端校验：明显错误（keepalive 不是两个整数、端口/数值越界、非法 CIDR）
// 在提交前拦下，不让它们打到 API。
// ---------------------------------------------------------------------------
const IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const CIDR_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
// DNS 允许 IPv4 或 IPv6（含 ':' 且只由十六进制、':' 、'.' 组成）
const IPV6ISH_RE = /^[0-9a-fA-F:.]+$/;

const ipv4Octets = (value) => {
  const match = IPV4_RE.exec(String(value ?? "").trim());
  if (!match) return null;
  const octets = match.slice(1, 5).map((part) => Number(part));
  return octets.every((part) => part <= 255) ? octets : null;
};

const isIpv4 = (value) => ipv4Octets(value) !== null;

const isIpAddress = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return false;
  if (isIpv4(text)) return true;
  return text.includes(":") && IPV6ISH_RE.test(text);
};

const isCidr = (value) => {
  const match = CIDR_RE.exec(String(value ?? "").trim());
  if (!match) return false;
  const octets = match.slice(1, 5).map((part) => Number(part));
  const prefix = Number(match[5]);
  return octets.every((part) => part <= 255) && prefix >= 0 && prefix <= 32;
};

// 掩码必须是连续的 1 后跟连续的 0（如 255.255.255.0），否则 OpenVPN 无法计算网段。
const isContiguousNetmask = (value) => {
  const octets = ipv4Octets(value);
  if (!octets) return false;
  const mask = ((octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]) >>> 0;
  const inverted = (~mask) >>> 0;
  return (inverted & (inverted + 1)) === 0;
};

const isIntegerInRange = (value, min, max) =>
  typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;

const splitMode = () => form.tunnel_mode === "split";

// 先去空白再校验，保证“校验通过的值”和“提交的值”完全一致
// （否则 " 10.8.44.0" 能通过校验，却会把带空格的字符串发给后端）。
const normalize = () => {
  form.keepalive = String(form.keepalive ?? "").trim();
  form.network = String(form.network ?? "").trim();
  form.netmask = String(form.netmask ?? "").trim();
  form.dns_servers = form.dns_servers.map((value) => String(value ?? "").trim());
  form.routes = form.routes.map((value) => String(value ?? "").trim());
};

const validate = () => {
  normalize();
  errors.port = "";
  errors.proto = "";
  errors.mssfix = "";
  errors.sndbuf = "";
  errors.rcvbuf = "";
  errors.keepalive = "";
  errors.max_clients = "";
  errors.network = "";
  errors.netmask = "";
  errors.dns = form.dns_servers.map(() => "");
  errors.routes = form.routes.map(() => "");

  const messages = [];

  if (!isIntegerInRange(form.port, 1, 65535)) {
    errors.port = "端口必须是 1-65535 的整数";
    messages.push(errors.port);
  }
  if (!form.proto) {
    errors.proto = "请选择协议";
    messages.push(errors.proto);
  }
  if (!/^\d+ \d+$/.test(String(form.keepalive ?? "").trim())) {
    errors.keepalive = "keepalive 必须是两个整数，如 “10 120”";
    messages.push(errors.keepalive);
  }
  if (!isIntegerInRange(form.mssfix, 0, 65535)) {
    errors.mssfix = "mssfix 必须是 0-65535 的整数";
    messages.push(errors.mssfix);
  }
  if (!isIntegerInRange(form.sndbuf, 0, BUFFER_MAX)) {
    errors.sndbuf = `sndbuf 必须是 0-${BUFFER_MAX} 的整数`;
    messages.push(errors.sndbuf);
  }
  if (!isIntegerInRange(form.rcvbuf, 0, BUFFER_MAX)) {
    errors.rcvbuf = `rcvbuf 必须是 0-${BUFFER_MAX} 的整数`;
    messages.push(errors.rcvbuf);
  }
  if (limitMaxClients.value && !isIntegerInRange(form.max_clients, 1, 65535)) {
    errors.max_clients = "最大连接数必须是 1-65535 的整数";
    messages.push(errors.max_clients);
  }
  if (!isIpv4(form.network)) {
    errors.network = "VPN 网段必须是 IPv4 地址，如 10.8.44.0";
    messages.push(errors.network);
  }
  if (!isContiguousNetmask(form.netmask)) {
    errors.netmask = "子网掩码必须是连续掩码，如 255.255.255.0";
    messages.push(errors.netmask);
  }

  form.dns_servers.forEach((value, index) => {
    if (!isIpAddress(value)) {
      errors.dns[index] = "DNS 地址格式不正确";
      messages.push(`DNS #${index + 1}：${errors.dns[index]}`);
    }
  });
  if (form.dns_servers.length > 2) {
    messages.push("DNS 服务器最多 2 个");
  }

  if (splitMode()) {
    form.routes.forEach((value, index) => {
      if (!isCidr(value)) {
        errors.routes[index] = "路由必须是 CIDR，如 192.168.1.0/24";
        messages.push(`路由 #${index + 1}：${errors.routes[index]}`);
      }
    });
  }

  return messages;
};

// ---------------------------------------------------------------------------
// 差量与风险提示
// ---------------------------------------------------------------------------
const buildPayload = () => {
  const before = JSON.parse(original.value || "{}");
  const payload = {};
  SETTINGS_KEYS.forEach((key) => {
    const previous = before[key] === undefined ? null : before[key];
    const current = form[key] === undefined ? null : form[key];
    if (JSON.stringify(previous) !== JSON.stringify(current)) {
      payload[key] = current;
    }
  });
  return payload;
};

// 高风险改动：返回给确认弹窗的静态说明文本（不包含任何用户输入）。
const collectRisks = () => {
  const before = JSON.parse(original.value || "{}");
  const risks = [];
  if (before.port !== form.port || before.proto !== form.proto) {
    risks.push(
      "<b>端口或协议已修改</b>：所有已签发的客户端 .ovpn 配置都会失效，客户端将无法连接，" +
        "保存后必须重新导出全部客户端并重新分发。",
    );
  }
  if (before.network !== form.network || before.netmask !== form.netmask) {
    risks.push(
      "<b>网段或掩码已修改</b>：NAT 规则和面板自身的 nginx 放行规则会被重建，OpenVPN 服务会重启；" +
        "已有客户端的固定 IP 可能需要同步更新。",
    );
  }
  return risks;
};

const confirmRisks = async (risks) => {
  const body = `<div>检测到以下高风险改动，请确认后继续：</div><ul>${risks
    .map((item) => `<li style="margin-top:6px">${item}</li>`)
    .join("")}</ul><div>保存前会自动备份 server.conf。</div>`;
  await ElMessageBox.confirm(body, "保存前请确认风险", {
    // 内容全部是本文件内的静态说明文本，不含接口数据或用户输入。
    dangerouslyUseHTMLString: true,
    type: "warning",
    confirmButtonText: "确认保存",
    cancelButtonText: "取消",
    closeOnClickModal: false,
  });
};

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
const applyData = (data) => {
  configPath.value = data?.path || "";
  exists.value = data?.exists !== false;
  // 三态处理：只有明确的 true / false 才当布尔值，缺失或 null 视为“未知”，
  // 避免后端没返回该字段时误报“不是本安装器创建”。
  const blockFlag = data ? data.managed_block_present : undefined;
  managedBlockPresent.value = blockFlag === undefined || blockFlag === null ? null : Boolean(blockFlag);

  const settings = data?.settings || {};
  SETTINGS_KEYS.forEach((key) => {
    if (settings[key] === undefined) return;
    if (key === "dns_servers" || key === "routes") {
      form[key] = Array.isArray(settings[key]) ? [...settings[key]] : [];
    } else {
      form[key] = settings[key];
    }
  });
  // 表单只绑定这 16 个键，快照也只存这些键，差量比较才不会被接口里的其他字段干扰。
  const snapshot = {};
  SETTINGS_KEYS.forEach((key) => {
    snapshot[key] = form[key] === undefined ? null : form[key];
  });
  original.value = JSON.stringify(snapshot);

  const ro = data?.readonly || {};
  Object.keys(readonlyInfo).forEach((key) => {
    readonlyInfo[key] = ro[key] === undefined ? null : ro[key];
  });

  errors.dns = form.dns_servers.map(() => "");
  errors.routes = form.routes.map(() => "");
  loaded.value = true;
};

const load = async () => {
  const { data } = await getServerSettings();
  applyData(data);
};

const reload = async (notify = false) => {
  loading.value = true;
  try {
    await load();
    if (notify) ElMessage.success("已重新加载");
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    loading.value = false;
  }
};

// ---------------------------------------------------------------------------
// 行编辑：路由 / DNS
// ---------------------------------------------------------------------------
const addRoute = () => {
  if (form.routes.length >= MAX_ROUTES) {
    ElMessage.warning(`路由最多 ${MAX_ROUTES} 条`);
    return;
  }
  form.routes.push("");
  errors.routes.push("");
};

const removeRoute = (index) => {
  form.routes.splice(index, 1);
  errors.routes.splice(index, 1);
};

const addDns = () => {
  if (form.dns_servers.length >= 2) {
    ElMessage.warning("最多只能配置 2 个 DNS 服务器");
    return;
  }
  form.dns_servers.push("");
  errors.dns.push("");
};

const removeDns = (index) => {
  form.dns_servers.splice(index, 1);
  errors.dns.splice(index, 1);
};

// ---------------------------------------------------------------------------
// 保存 / 重新导出
// ---------------------------------------------------------------------------
const handleSave = async () => {
  const messages = validate();
  if (messages.length) {
    ElMessage.error(messages[0]);
    return;
  }
  const payload = buildPayload();
  if (Object.keys(payload).length === 0) {
    ElMessage.info("没有检测到需要保存的修改");
    return;
  }
  try {
    // 先置为提交中，避免确认弹窗打开期间被重复点击触发第二次保存。
    saving.value = true;
    const risks = collectRisks();
    if (risks.length) {
      await confirmRisks(risks);
    }
    const { data } = await updateServerSettings(payload);
    lastApply.value = {
      ...data,
      changed: Array.isArray(data?.changed) && data.changed.length ? data.changed : Object.keys(payload),
    };
    reexportResult.value = null;
    if (data?.clients_affected) {
      ElMessage.warning("端口/协议已变更，请重新导出全部客户端配置");
    } else if (data?.network_changed) {
      ElMessage.warning("网段已变更：NAT 规则与 nginx 放行规则已重建");
    }
    ElMessage.success(data?.message || "已保存");
    try {
      // 保存成功后重新读取，受管块状态/path 等随之刷新；失败不影响“已保存”的结论。
      await load();
    } catch (refreshError) {
      ElMessage.warning("已保存，但重新读取配置失败，请点击“重新加载”确认");
    }
  } catch (e) {
    if (e === "cancel" || e === "close") return;
    ElMessage.error(e.message);
  } finally {
    saving.value = false;
  }
};

const handleReexportAll = async () => {
  reexporting.value = true;
  try {
    const { data } = await reexportAllClients();
    const exported = data?.exported ?? 0;
    const failed = Array.isArray(data?.failed) ? data.failed : [];
    reexportResult.value = { exported, failed };
    if (failed.length) {
      ElMessage.warning(`重新导出完成：成功 ${exported} 个，失败 ${failed.length} 个`);
    } else {
      ElMessage.success(`已重新导出 ${exported} 个客户端配置`);
    }
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    reexporting.value = false;
  }
};

onMounted(() => reload(false));
</script>

<style scoped>
.path-label {
  margin-right: var(--space-1);
  color: var(--text-subtle);
}

.page-header__subtitle code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: var(--fs-12);
  word-break: break-all;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  align-items: start;
}

.control-field {
  width: 100%;
  max-width: 220px;
}

.rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.row-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.row-input {
  flex: 1 1 180px;
  width: 100%;
  max-width: 240px;
}

.inline-number {
  margin-left: var(--space-3);
}

.muted {
  color: var(--text-subtle);
}

.muted.hint {
  display: inline-block;
  margin-left: var(--space-2);
  font-size: var(--fs-12);
}

.apply-line {
  margin-bottom: var(--space-2);
  font-size: var(--fs-13);
  color: var(--text);
}

.apply-line .label {
  margin-right: 6px;
  color: var(--text-muted);
}

.tag-gap {
  margin-right: 6px;
}

.failed-list {
  margin: var(--space-2) 0 0 var(--space-4);
  color: var(--danger);
  font-size: var(--fs-12);
}

.readonly-hint {
  margin-bottom: var(--space-3);
  font-size: var(--fs-13);
}

.net-warning {
  margin-top: var(--space-1);
}

.save-bar {
  position: sticky;
  bottom: 0;
  z-index: 10;
  margin: 0 calc(var(--space-4) * -1) calc(var(--space-4) * -1);
  padding: var(--space-3) var(--space-4);
  background: var(--surface);
  border-top: 1px solid var(--border);
  box-shadow: 0 -4px 12px rgba(16, 24, 40, 0.06);
}

.save-bar .el-button {
  width: 100%;
}

@media (max-width: 1199.98px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 767.98px) {
  .settings-form .el-input-number {
    width: 100%;
  }

  .control-field,
  .row-input {
    max-width: none;
  }

  .inline-number {
    margin-left: 0;
  }
}
</style>
