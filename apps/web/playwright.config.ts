import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const web = dirname(fileURLToPath(import.meta.url));
const root = resolve(web, "..", "..");
const pythonPath = join(root, ".venv", process.platform === "win32" ? "Scripts" : "bin", process.platform === "win32" ? "python.exe" : "python");
const python = existsSync(pythonPath) ? `"${pythonPath}"` : "python";
const apiPort = Number(process.env.TACTIQ_E2E_API_PORT ?? 18765);
const webPort = Number(process.env.TACTIQ_E2E_WEB_PORT ?? 13000);
if (![apiPort, webPort].every((port) => Number.isInteger(port) && port >= 1024 && port <= 65535)) {
  throw new Error("Browser-test ports must be non-privileged integer ports");
}
const api = `http://127.0.0.1:${apiPort}`;
const baseURL = `http://127.0.0.1:${webPort}`;
const production = process.env.TACTIQ_E2E_PRODUCTION === "true";
process.env.TACTIQ_E2E_API_URL = api;

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60_000,
  outputDir: process.env.TACTIQ_E2E_OUTPUT_DIR ?? "./test-results",
  reporter: [
    ["list"],
    ["json", { outputFile: process.env.TACTIQ_E2E_REPORT_PATH ?? "./test-results/results.json" }],
  ],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--enable-unsafe-swiftshader"] },
  },
  webServer: [
    {
      command: `${python} -m uvicorn tactiq.presentation.api.main:app --app-dir services/backend --host 127.0.0.1 --port ${apiPort}`,
      cwd: root,
      url: `${api}/health/ready`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        APP_ENV: "local", FOUNDRY_ENABLED: "false", TACTIQ_AUTH_ENABLED: "false",
        TACTIQ_CV_DETECTOR: "motion",
        AZURE_SPEECH_API_KEY: "", AZURE_SPEECH_KEY: "", AZURE_FOUNDRY_API_KEY: "",
        TACTIQ_CORS_ALLOW_ORIGINS: baseURL,
        TACTIQ_MEDIA_DIR: process.env.TACTIQ_E2E_MEDIA_DIR ?? join(root, ".artifacts", "e2e-media"),
        TACTIQ_DATABASE_PATH: process.env.TACTIQ_E2E_DATABASE_PATH ?? join(root, ".artifacts", "e2e.sqlite3"),
        TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE: "120",
      },
    },
    {
      command: `npm run ${production ? "start" : "dev"} -- --hostname 127.0.0.1 --port ${webPort}`,
      cwd: web,
      url: baseURL,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        NEXT_PUBLIC_API_BASE_URL: api, TACTIQ_BACKEND_URL: api,
        NEXT_PUBLIC_ENABLE_CLOUD_SPEECH: "true",
      },
    },
  ],
});
