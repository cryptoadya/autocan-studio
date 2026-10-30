import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  timeout: 45_000,
  forbidOnly: !!process.env.CI,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    viewport: {width: 1280, height: 900},
    launchOptions: {chromiumSandbox: true, slowMo: process.env.AUTOCAN_DEMO === '1' ? 1200 : 0},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'on',
  },
  webServer: {
    command: 'uv run --locked python frontend/e2e/serve.py',
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    url: 'http://127.0.0.1:8765/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
