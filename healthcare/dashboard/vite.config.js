import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 5173 is not incidental: it is one of the origins the API allows by
// default (see ALLOWED_ORIGINS in api/main.py). Changing it here means
// changing DASHBOARD_ORIGINS there too, or the browser blocks every fetch.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
