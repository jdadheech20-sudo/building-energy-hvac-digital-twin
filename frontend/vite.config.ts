import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";

const apiProxy = process.env.CAMPUS_API_PROXY ?? "http://127.0.0.1:8000";
const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [repositoryRoot] },
    port: 5173,
    proxy: { "/api": apiProxy },
  },
});
