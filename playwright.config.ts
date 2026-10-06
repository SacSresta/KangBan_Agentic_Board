import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  workers: 1,
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:8765', trace: 'retain-on-failure', headless: true, actionTimeout: 10000 },
  reporter: 'list',
});
