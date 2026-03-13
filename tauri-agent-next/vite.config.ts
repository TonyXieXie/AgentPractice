import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
// @ts-expect-error process is a nodejs global
const proxyTarget = process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      "^/(healthz|config|observe|runs|ws)": {
        target: proxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
