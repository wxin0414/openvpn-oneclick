<template>
  <div class="dl">
    <div class="dl__grid">
      <a
        v-for="item in platforms"
        :key="item.key"
        class="dl__card"
        :href="item.url"
        target="_blank"
        rel="noopener noreferrer"
      >
        <span class="dl__os">{{ item.os }}</span>
        <span class="dl__app">{{ item.app }}</span>
        <span class="dl__note">{{ item.note }}</span>
        <span class="dl__go">前往下载 →</span>
      </a>
    </div>
    <p class="dl__tip">
      先装客户端，再导入 <code>.ovpn</code> 文件或直接扫二维码。
      国内应用商店搜不到时，用上面的官网链接下载（官网会按平台跳到对应商店/安装包）。
    </p>
    <p class="dl__tip dl__tip--warn">
      这里是<strong>官方</strong>客户端的分发地址。第三方「一键连 VPN」类应用不要用来导入本服务的配置。
    </p>
  </div>
</template>

<script setup>
// 平台清单刻意保持为纯数据：以后加平台只改这个数组，不动模板。
// 链接只放官方来源（openvpn.net / Google Play / Tunnelblick），不放第三方镜像。
// iOS 没有硬编码 App Store 数字 ID —— 没把握的链接不如给官方分发页。
const platforms = [
  {
    key: "windows",
    os: "Windows",
    app: "OpenVPN Connect",
    note: "官方客户端，支持导入 .ovpn",
    url: "https://openvpn.net/client/",
  },
  {
    key: "android",
    os: "Android",
    app: "OpenVPN Connect",
    note: "Google Play（包名 net.openvpn.openvpn）",
    url: "https://play.google.com/store/apps/details?id=net.openvpn.openvpn",
  },
  {
    key: "ios",
    os: "iOS / iPadOS",
    app: "OpenVPN Connect",
    note: "经官网跳到 App Store",
    url: "https://openvpn.net/client/",
  },
  {
    key: "macos",
    os: "macOS",
    app: "OpenVPN Connect",
    note: "另可用 Tunnelblick（社区版）",
    url: "https://openvpn.net/client/",
  },
  {
    key: "linux",
    os: "Linux",
    app: "openvpn 命令行",
    note: "apt install openvpn；图形界面见官方页",
    url: "https://openvpn.net/community-downloads/",
  },
];
</script>

<style scoped>
.dl {
  font-size: 13px;
}

.dl__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}

.dl__card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s, background-color 0.15s;
}

.dl__card:hover {
  border-color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.dl__os {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.dl__app {
  color: var(--el-color-primary);
}

.dl__note {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.dl__go {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dl__tip {
  margin: 10px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.dl__tip--warn {
  color: var(--el-color-warning);
}

.dl code {
  padding: 0 3px;
  background: var(--el-fill-color-light);
  border-radius: 3px;
}
</style>
