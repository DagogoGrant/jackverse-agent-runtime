import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  globalTeardown: './tests/e2e/global-teardown.ts',
  webServer: [
    {
      command:
        'rm -f /tmp/caseworker_e2e_integration.db /tmp/caseworker_e2e_integration.db-wal /tmp/caseworker_e2e_integration.db-shm && CASEWORKER_DEV_AUTH=true CASEWORKER_DB_PATH=/tmp/caseworker_e2e_integration.db PYTHONPATH=../src python3 -m uvicorn caseworker.api:create_app --factory --host 127.0.0.1 --port 8089',
      port: 8089,
      timeout: 30000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        'VITE_JACKVERSE_DEV_AUTH=true VITE_ENABLE_PROTOTYPES=true VITE_CASEWORKER_API_URL=http://127.0.0.1:8089 npm run dev -- --port 3001',
      port: 3001,
      timeout: 30000,
      reuseExistingServer: !process.env.CI,
    },
  ],
  use: {
    baseURL: 'http://127.0.0.1:3001',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
      },
    },
  ],
});
