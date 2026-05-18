import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev: Vite on 5173 proxies /api → backend on STUDIO_BACKEND_URL (default 8080).
// Prod (M7): the FastAPI app serves the built `dist/` directly, so no proxy.
const backend = process.env.STUDIO_BACKEND_URL || "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
      },
    },
  },
});
