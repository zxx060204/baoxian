import { describe, expect, it } from 'vitest';
import { formatBeijingTime } from '../src/time';

describe('中台时间展示', () => {
  it('将 UTC 时间按北京时间显示', () => {
    expect(formatBeijingTime('2026-11-09T05:51:41.000Z')).toBe('2026/11/09 13:51:41');
  });
});
