import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Team247 chat frontend.
//
// Dev: `npm run dev` starts Vite on :5173 and proxies /api/* → the FastAPI
// server on :8000, so the SPA's fetch('/api/...') calls are same-origin in
// both dev (proxied) and prod (served by FastAPI). No CORS either way.
//
// Prod: `npm run build` emits the bundle into ../app/static so FastAPI serves
// it at / with no copy step (see app/main.py `_STATIC`).
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
