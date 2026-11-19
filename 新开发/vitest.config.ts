import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['apps/web/test/**/*.test.{ts,tsx}'],
    pool: 'threads',
    fileParallelism: false,
    maxWorkers: 1,
    minWorkers: 1,
  },
});
