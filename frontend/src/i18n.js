import { ref } from "vue";

// 语言状态的**单一来源**。
//
// 之前只有 App.vue 内部一张小表，只覆盖 8 个导航项；按钮和表单标签仍是硬编码中文。
// 现在把字典挪到这里，视图用 t("中文") 取英文——key 直接用中文原文，好处是：
//   * 漏翻时不会显示奇怪的 key，而是回退到中文（可读、可用）
//   * 新增文案不用先想 key 名
//   * 搜代码能直接搜到中文原文
//
// 覆盖范围：导航 + 各页主要按钮与常见表单标签。提示语、错误信息仍是中文（后端返回的
// detail 也是中文），这一点在 README 里写清楚了，不假装全站多语言。

const STORAGE_KEY = "lang";

export const lang = ref(localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh");

const EN = {
  // 导航
  仪表盘: "Dashboard",
  服务器管理: "Server",
  客户端管理: "Clients",
  服务端设置: "OpenVPN Settings",
  面板设置: "Panel Settings",
  日志审计: "Audit Logs",
  用户管理: "Users",
  通知设置: "Notifications",

  // 通用按钮
  刷新: "Refresh",
  查询: "Search",
  重置: "Reset",
  保存: "Save",
  取消: "Cancel",
  删除: "Delete",
  编辑: "Edit",
  下载: "Download",
  分享: "Share",
  登录: "Sign in",
  退出登录: "Sign out",
  导出: "Export",

  // 客户端页
  新增客户端: "Add client",
  客户端下载: "Client apps",
  导出清单: "Export list",
  导出配置包: "Export configs",
  禁用: "Disable",
  启用: "Enable",
  按名称搜索: "Search by name",
  状态: "Status",
  分组: "Group",
  未分组: "Ungrouped",
  在线: "Online",
  离线: "Offline",

  // 用户与 2FA
  新增用户: "Add user",
  两步验证: "Two-factor auth",
  生成密钥: "Generate secret",
  确认启用: "Confirm and enable",
  关闭2FA: "Disable 2FA",
  修改我的口令: "Change my password",
  重置口令: "Reset password",
  停用: "Deactivate",
  账号列表: "Accounts",

  // 备份与通知
  立即备份: "Back up now",
  刷新列表: "Refresh list",
  恢复: "Restore",
  定时备份: "Scheduled backup",
  启用定时备份: "Enable scheduled backup",
  间隔小时: "Interval (hours)",
  保留份数: "Keep copies",
  立即执行一次: "Run once now",
  发送测试: "Send test",
  最低推送等级: "Minimum level",
  冷却时间秒: "Cooldown (seconds)",

  // 面板设置
  面板设置标题: "Panel settings",
  一键诊断: "Diagnostics",
  在线升级: "Online upgrade",
  管理员凭据: "Admin credentials",

  // 用户管理 / 2FA / 日志 / OpenVPN 控制
  新增用户: "Add user",
  重置口令: "Reset password",
  停用: "Deactivate",
  生成密钥: "Generate secret",
  确认启用: "Confirm and enable",
  修改我的口令: "Change my password",
  保存并重新登录: "Save and sign out",
  查询日志: "Search logs",
  导出审计: "Export audit",
  停止: "Stop",
  启动: "Start",
  重启: "Restart",
  添加通知: "Add notification",
};

export function t(text) {
  if (lang.value === "zh") return text;
  return EN[text] || text;
}

export function setLang(next) {
  lang.value = next === "en" ? "en" : "zh";
  localStorage.setItem(STORAGE_KEY, lang.value);
}

export function toggleLang() {
  setLang(lang.value === "en" ? "zh" : "en");
}
