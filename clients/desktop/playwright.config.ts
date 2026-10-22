import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/window',
  timeout: 60000,
  workers: 1,
  reporter: 'list',
});
