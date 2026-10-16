import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./assets/base.css";

// Element Plus is loaded on demand by unplugin-auto-import /
// unplugin-vue-components (see vite.config.js):
//   - <el-*> components -> unplugin-vue-components + ElementPlusResolver,
//     which imports each component's own stylesheet.
//   - ElMessage / ElMessageBox are imported explicitly (named imports) by the
//     views, so unplugin-auto-import does NOT rewrite those import statements
//     and the resolver's automatic importStyle hint never runs for them.
//     Their stylesheets therefore have to be pulled in by hand. Without these
//     two imports the toasts and confirmation dialogs render unstyled.
// Only these two are used in the app; add e.g. el-notification.css here if
// ElNotification is ever adopted.
import "element-plus/theme-chalk/el-message.css";
import "element-plus/theme-chalk/el-message-box.css";

// NOTE: neither `import ElementPlus from "element-plus"` (full component
// registration) nor `import "element-plus/dist/index.css"` (the ~353 kB
// monolithic stylesheet) is used any more.
const app = createApp(App);
app.use(router);
app.mount("#app");
