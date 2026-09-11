const { defineConfig, devices } = require('@playwright/test');
const os = require('node:os');
const path = require('node:path');

const port = Number(process.env.E2E_PORT || 18765);
const dbPath = path.join(os.tmpdir(), `tsumugi-e2e-${process.pid}.sqlite3`);
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

process.env.TSUMUGI_E2E_DB_PATH = dbPath;

module.exports = defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  globalTeardown: require.resolve('./e2e/global-teardown'),
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `${python} server.py --port ${port} --db ${JSON.stringify(dbPath)}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 15_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
