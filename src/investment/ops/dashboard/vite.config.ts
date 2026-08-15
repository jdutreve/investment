import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built to `dist/`, which is exactly where `ops/api.py` looks (its DIST_DIR).
// No dev server in the shipped path (ADR-005 amendment, M10): the agent's own
// aiohttp process serves these files, so the page is same-origin with the API
// and the X-Ops-Token scheme keeps working.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Hashed filenames under /assets — the one path api.py mounts as static.
    assetsDir: "assets",
  },
});
