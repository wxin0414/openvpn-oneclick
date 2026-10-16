import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus on demand.
    // AutoImport covers the programmatic APIs (ElMessage, ElMessageBox,
    // ElNotification, ...) that are NOT resolved as components; Components
    // covers the <el-*> template tags. Both resolvers import the matching
    // per-component stylesheet, which replaces the full
    // `element-plus/dist/index.css` (~353 kB) previously pulled in by main.js.
    // dts: false - this project is plain JavaScript, no type declarations needed.
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: false,
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: false,
    }),
  ],
  base: "/ovpnmanager/",
  server: {
    host: "0.0.0.0",
    port: 8010,
  },
  build: {
    target: "es2018",
    outDir: "ovpnmanager",
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Split the previously monolithic 1 MB vendor chunk. Element Plus is
        // separated from the always-needed framework/runtime code so that the
        // vue/router/axios bundle can be cached independently of the (much
        // larger, on-demand) component library.
        manualChunks: {
          vue: ["vue", "vue-router"],
          axios: ["axios"],
          "element-plus": ["element-plus", "lodash-es"],
        },
      },
    },
  },
});
