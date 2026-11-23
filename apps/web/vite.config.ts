import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const appDir = path.dirname(fileURLToPath(import.meta.url));

function resolveAppVersion() {
  if (process.env.VITE_APP_VERSION) {
    return process.env.VITE_APP_VERSION.trim();
  }
  try {
    return execSync("git rev-parse --short HEAD", { cwd: appDir, encoding: "utf8" }).trim();
  } catch {
    return "dev";
  }
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // New content-addressed URLs supersede previously cached asset errors.
        entryFileNames: "assets/[name]-[hash:12].js",
        chunkFileNames: "assets/[name]-[hash:12].js",
        assetFileNames: "assets/[name]-[hash:12][extname]",
      },
    },
  },
  define: {
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(resolveAppVersion()),
  },
  resolve: {
    alias: {
      "@": path.resolve(appDir, "src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
