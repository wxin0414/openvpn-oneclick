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
            <el-input-number v-model="form.mssfix" :min="0" :max="MSSFIX_MAX" :step="1" controls-position="right" />
            <div v-if="errors.mssfix" class="field-tip err">{{ errors.mssfix }}</div>
            <div v-else class="field-tip">0-1500，用于避免分片，常见值 1420。</div>
          </el-form-item>
          <el-form-item label="加密套件 (data-ciphers)">
            <el-input v-model="form.data_ciphers" placeholder="AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305" />
            <div v-if="errors.data_ciphers" class="field-tip err">{{ errors.data_ciphers }}</div>
            <div v-else class="field-tip">
              冒号分隔，可选 AES-256-GCM / AES-128-GCM / CHACHA20-POLY1305 / AES-256-CBC / AES-128-CBC。
              改了之后已发出的 .ovpn 需要重新导出。
            </div>
          </el-form-item>

          <el-form-item label="认证摘要 (auth)">
            <el-select v-model="form.auth" style="width: 100%">
              <el-option label="SHA512" value="SHA512" />
              <el-option label="SHA256（推荐）" value="SHA256" />
              <el-option label="SHA1" value="SHA1" />
              <el-option label="none" value="none" />
            </el-select>
            <div class="field-tip">只有全部套件都是 AEAD（GCM/CHACHA20）时 none 才有意义。</div>
          </el-form-item>
          <el-form-item label="运行用户 (user)">
            <el-input v-model="form.user" placeholder="nobody" />
            <div class="field-tip">OpenVPN 初始化完成后降权到这个用户；写错会让服务起不来（助手会回滚）。</div>
          </el-form-item>

          <el-form-item label="运行用户组 (group)">
            <el-input v-model="form.group" placeholder="nogroup" />
            <div class="field-tip">与 user 配套；Debian 上是 nogroup，RHEL 系通常是 nobody。</div>
          </el-form-item>
          <el-form-item label="日志文件 (log-append)">
            <el-input v-model="form.log_append" placeholder="/var/log/openvpn/openvpn.log" />
            <div class="field-tip">必须位于 /var/log/openvpn/ 下：OpenVPN 以降权用户运行，只有该目录被授权写入。</div>
          </el-form-item>

          <el-form-item label="状态文件 (status)">
            <el-input v-model="form.status_file" placeholder="/var/log/openvpn/openvpn-status.log" />
            <div class="field-tip">面板的在线客户端列表依赖它，同样必须在 /var/log/openvpn/ 下。</div>
          </el-form-item>

          <el-form-item label="TLS 重协商间隔 (reneg-sec)">
            <el-input-number v-model="form.reneg_sec" :min="0" :max="604800" :step="60" controls-position="right" />
            <div v-if="errors.reneg_sec" class="field-tip err">{{ errors.reneg_sec }}</div>
            <div v-else class="field-tip">秒。0 = 禁用重协商（长连接更稳但安全性略降）。留空则用 OpenVPN 默认（3600）。</div>
          </el-form-item>

          <el-form-item label="最低 TLS 版本 (tls-version-min)">
            <el-select v-model="form.tls_version_min" clearable placeholder="不设置（用 OpenVPN 默认）" style="width: 100%">
              <el-option label="TLS 1.3（最严）" value="1.3" />
              <el-option label="TLS 1.2（推荐）" value="1.2" />
              <el-option label="TLS 1.1（不安全）" value="1.1" />
              <el-option label="TLS 1.0（不安全）" value="1.0" />
            </el-select>
            <div v-if="errors.tls_version_min" class="field-tip err">{{ errors.tls_version_min }}</div>
            <div v-else class="field-tip">禁掉旧版 TLS。注意：客户端 OpenSSL 太老时会直接连不上，改前先确认客户端版本。</div>
          </el-form-item>

          <el-form-item label="日志详细度 (verb)">
            <el-input-number v-model="form.verb" :min="0" :max="11" controls-position="right" />
            <div v-if="errors.verb" class="field-tip err">{{ errors.verb }}</div>
            <div v-else class="field-tip">0-11，默认 3。排查连接问题时临时调到 4-6，事后记得改回。</div>
          </el-form-item>

          <el-form-item label="重复日志抑制 (mute)">
            <el-input-number v-model="form.mute" :min="0" :max="10000" controls-position="right" />
            <div v-if="errors.mute" class="field-tip err">{{ errors.mute }}</div>
            <div v-else class="field-tip">同类消息最多显示 N 条，防止日志被刷爆。</div>
          </el-form-item>

          <el-form-item label="空闲断开 (inactive)">
            <el-input-number v-model="form.inactive" :min="0" :max="86400" :step="60" controls-position="right" />
            <div v-if="errors.inactive" class="field-tip err">{{ errors.inactive }}</div>
            <div v-else class="field-tip">秒；0 = 禁用。空闲超过该时间自动断开，配合「最大连接数」回收名额。</div>
          </el-form-item>

          <el-form-item label="阻断隧道外 DNS (block-outside-dns)">
            <el-switch v-model="form.block_outside_dns" active-text="开启" inactive-text="关闭" />
            <div class="field-tip">
              Windows 客户端默认会绕过隧道、直接用本地网卡的 DNS（DNS 泄漏）。
              开启后把这些查询也拉进隧道。**仅对 Windows 有意义**，其他系统会忽略这条 push。
            </div>
          </el-form-item>
          <el-form-item label="发送缓冲区 (sndbuf)" required>
            <el-input-number v-model="form.sndbuf" :min="0" :max="BUFFER_MAX" :step="1024" controls-position="right" />
            <div v-if="errors.sndbuf" class="field-tip err">{{ errors.sndbuf }}</div>
            <div v-else class="field-tip">字节，默认 393216。</div>
          </el-form-item>
          <el-form-item label="接收缓冲区 (rcvbuf)" required>
            <el-input-number v-model="form.rcvbuf" :min="0" :max="BUFFER_MAX" :step="1024" controls-position="right" />
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
              :max="MAX_CLIENTS_MAX"
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
        <el-descriptions-item label="cipher（旧版对称加密）">{{ display(readonlyInfo.cipher) }}</el-descriptions-item>
      </el-descriptions>
    </section>

    <!-- 手机端把主操作固定在视口底部，长表单滚动时也能随时保存 -->
    <div v-if="isMobile" class="save-bar">
      <el-button type="primary" :loading="saving" :disabled="saving || loading" @click="handleSave">
        保存并应用
      </el-button>
    </div>
        <el-card class="panel-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>配置历史（server.conf 备份）</span>
            <el-button size="small" @click="loadHistory" :loading="historyLoading">{{ $t("刷新") }}</el-button>
          </div>
        </template>
        <div class="field-tip" style="margin-bottom: 8px">
          每次保存设置，root 助手都会把旧配置留成一份备份。可以查看与当前的差异，或直接用某份备份回滚
          （回滚同样会先备份当前配置，并在服务起不来时自动还原）。
        </div>
        <div v-if="!historyItems.length" class="field-tip">暂无备份。</div>
        <el-table v-else :data="historyItems" size="small" max-height="320">
          <el-table-column prop="name" label="备份文件" min-width="240" />
          <el-table-column prop="mtime" label="时间（UTC）" width="200" />
          <el-table-column prop="size" label="大小" width="90" />
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button size="small" @click="showDiff(row)">差异</el-button>
              <el-button size="small" type="warning" @click="restore(row)">回滚</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-dialog v-model="diffVisible" :title="'差异：' + diffName" width="min(94vw, 820px)">
        <div v-if="diffLoading" v-loading="true" style="height: 160px"></div>
        <template v-else>
          <div class="field-tip" style="margin-bottom: 6px">
            左边是当前配置（-），右边是该备份（+）。{{ diffIdentical ? "两者内容相同。" : "" }}
          </div>
          <pre class="diff-view">{{ diffText || "（无差异）" }}</pre>
          <div v-if="diffTruncated" class="field-tip">差异过长，已截断显示。</div>
        </template>
      </el-dialog>
</div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getServerSettings, reexportAllClients, updateServerSettings } from "../api/settings";
import { useBreakpoint } from "../composables/useBreakpoint";
import api from "../api/http";

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
  "data_ciphers",
  "auth",
  "user",
  "group",
  "log_append",
  "status_file",
  "reneg_sec",
  "tls_version_min",
  "verb",
  "mute",
  "inactive",
  "block_outside_dns",
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
// 数值上限必须与后端 server_conf.py 的 validate_editable 完全一致：
// 后端 mssfix 是 0-1500、其余数值项是 0-10485760。前端写宽了，页面就能
// 提交出必然被 400 拒绝的值，用户只看到「保存失败」而不知道错在哪。
const MSSFIX_MAX = 1500;
const BUFFER_MAX = 10485760;
const MAX_CLIENTS_MAX = 10485760;
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
  data_ciphers: "",
  auth: "",
  user: "",
  group: "",
  log_append: "",
  status_file: "",
  // 这五项是可选调优：null 表示「不写进配置，用 OpenVPN 默认」（0 有真实含义，不能当未设置）
  reneg_sec: null,
  tls_version_min: null,
  verb: null,
  mute: null,
  inactive: null,
  block_outside_dns: false,
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
  if (!isIntegerInRange(form.mssfix, 0, MSSFIX_MAX)) {
    errors.mssfix = `mssfix 必须是 0-${MSSFIX_MAX} 的整数`;
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
  if (limitMaxClients.value && !isIntegerInRange(form.max_clients, 1, MAX_CLIENTS_MAX)) {
    errors.max_clients = `最大连接数必须是 1-${MAX_CLIENTS_MAX} 的整数`;
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
  if (before.user !== form.user || before.group !== form.group) {
    risks.push(
      "<b>运行用户/组已修改</b>：OpenVPN 启动后会降权到该用户。若该用户不存在、或降权后" +
        "读不到证书 / 写不了日志，服务会起不来——助手会自动回滚并恢复原配置，" +
        "但期间隧道会中断几秒。",
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

// ---- 配置历史与回滚 ----
const historyItems = ref([]);
const historyLoading = ref(false);
const diffVisible = ref(false);
const diffLoading = ref(false);
const diffName = ref("");
const diffText = ref("");
const diffTruncated = ref(false);
const diffIdentical = ref(false);

async function loadHistory() {
  historyLoading.value = true;
  try {
    const { data } = await api.get("/settings/history");
    historyItems.value = data.items || [];
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "读取配置历史失败");
  } finally {
    historyLoading.value = false;
  }
}

async function showDiff(row) {
  diffVisible.value = true;
  diffLoading.value = true;
  diffName.value = row.name;
  diffText.value = "";
  try {
    const { data } = await api.get(`/settings/history/${row.name}`);
    diffText.value = data.diff || "";
    diffTruncated.value = !!data.diff_truncated;
    diffIdentical.value = !!data.identical;
  } catch (err) {
    diffText.value = err?.response?.data?.detail || "读取差异失败";
  } finally {
    diffLoading.value = false;
  }
}

async function restore(row) {
  const { value } = await ElMessageBox.prompt(
    `将用「${row.name}」覆盖当前 server.conf，并重启 OpenVPN。如果新配置起不来会自动还原。请输入 RESTORE 确认`,
    "回滚配置",
    {
      inputPlaceholder: "RESTORE",
      confirmButtonText: "确认回滚",
      cancelButtonText: "取消",
      closeOnClickModal: false,
    },
  ).catch(() => ({ value: null }));
  if (value === null) return;
  if ((value || "").trim() !== "RESTORE") {
    ElMessage.warning("确认文本不匹配，已取消");
    return;
  }
  try {
    const { data } = await api.post(`/settings/history/${row.name}/restore`, null, {
      params: { confirm: "RESTORE" },
    });
    ElMessage.success(data.message || "已回滚");
    await loadHistory();
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "回滚失败");
  }
}
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
.diff-view {
  max-height: 420px;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 10px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre;
}
</style>
