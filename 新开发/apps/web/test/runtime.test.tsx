// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../src/App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

describe('后端运行时信息', () => {
  it.each([['python', '3.14.0', 'Python 3.14.0'], ['node', 'v24.0.0', 'Node v24.0.0']])('正确显示 %s 后端版本', async (key, version, expected) => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url === '/health' ? { status: 'ok', service: 'baoxian-api', version: '0.1.0' } : { service: 'baoxian-api', version: '0.1.0', uptimeSeconds: 1, [key]: version } })));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '检查认证' }));
    expect(await screen.findByText(new RegExp(expected.replace('.', '\\.')))).toBeTruthy();
  });
});
