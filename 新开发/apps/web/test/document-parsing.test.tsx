// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ProductsPage from '../src/ProductsPage';

const materials = [
  { type: 'clause', label: '条款', status: 'downloaded' },
  { type: 'rate_table', label: '费率表', status: 'downloaded' },
  { type: 'cash_value_table', label: '现金价值表', status: 'downloaded' },
  { type: 'prospectus', label: '说明书', status: 'downloaded' },
];
const product = {
  id: 130,
  name: '测试产品',
  companyName: '友邦',
  salesStatus: 'on_sale',
  associationRecords: [],
  history: [],
  materials,
  sources: materials.map(({ type }, index) => ({ documentId: index + 10, id: index + 20, type })),
};
const table = {
  table_id: 'table-1', page: 2, row_count: 2, column_count: 3,
  header_rows: [0], structure_status: 'partial', continuation_of: null,
  grid: [[['年龄', 2, 1], ['保险费', 1, 2], -1], [-1, ['1,200.00', 1, 1], null]],
};
const numeric = {
  schema: 'mapping-input-v1', pages: [{ page: 1 }, { page: 2 }],
  contexts: [
    { context_id: 'a', text: '缴费方式见首页', page: 1 },
    { context_id: 'b', text: '交费期间：趸交（性别：女）', page: 2,
      relations: [{ table_id: 'table-1', position: 'before' }] },
  ],
  tables: [table],
};
const narrative = {
  schema: 'document-content-v1', status: 'partial',
  pages: [{ page_number: 1, status: 'ready' }, { page_number: 2, status: 'needs_ocr' }],
  chunks: [{ chunk_id: 'c', kind: 'text', raw_text: '保障范围与保险责任', page_number: 1 }],
  tables: [],
};
const brochure = {
  ...narrative,
  chunks: [...narrative.chunks, { chunk_id: 't', kind: 'table_reference', page_number: 1, table_id: 'table-1' }],
  tables: [{ ...table, page: 1 }],
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openProduct(getLayout: (url: string, options?: RequestInit) => Response | Promise<Response>,
  savedProductLayouts?: (options?: RequestInit) => Response) {
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/companies') return Response.json({ items: [] });
    if (url.startsWith('/api/products?')) return Response.json({ items: [product] });
    if (url === '/api/products/130') return Response.json(product);
    expect(options?.headers).toEqual({ Authorization: 'Bearer test' });
    if (url === '/api/products/130/parsed-layouts') return savedProductLayouts?.(options) ?? Response.json({ items: [] });
    return getLayout(url, options);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ProductsPage token="test" />);
  fireEvent.click(await screen.findByRole('button', { name: '详情' }));
  return { panel: within(await screen.findByRole('region', { name: '资料解析' })), fetchMock };
}

it('从产品详情启动四类解析并按页展示网格、正文、演示表和 OCR 状态', async () => {
  const layouts: Record<string, unknown> = {
    '/api/documents/10/saved-layout': narrative,
    '/api/documents/11/saved-layout': numeric,
    '/api/documents/12/saved-layout': numeric,
    '/api/documents/13/saved-layout': brochure,
  };
  const { panel, fetchMock } = await openProduct((url, options) => {
    expect(layouts[url]).toBeDefined();
    expect(options?.method).toBe('POST');
    return Response.json(layouts[url]);
  });
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith('/api/documents/'))).toBe(false);

  for (const label of ['费率表', '现金价值表']) {
    fireEvent.click(panel.getByRole('button', { name: `解析${label}` }));
    const grid = await panel.findByRole('table', { name: /第 2 页 · 表 1/ });
    expect(within(grid).getByRole('columnheader', { name: '年龄' }).getAttribute('rowspan')).toBe('2');
    expect(within(grid).getByRole('columnheader', { name: '保险费' }).getAttribute('colspan')).toBe('2');
    expect(within(grid).getAllByRole('cell')).toHaveLength(2);
    expect(within(grid).getByText('网格缺口')).toBeTruthy();
    const context = panel.getByText('交费期间：趸交（性别：女）');
    expect(context.nextElementSibling?.contains(grid)).toBe(true);
    fireEvent.change(panel.getByLabelText('原件页码'), { target: { value: '1' } });
    expect(panel.getByText('缴费方式见首页')).toBeTruthy();
    expect(panel.queryByRole('table')).toBeNull();
  }

  fireEvent.click(panel.getByRole('button', { name: '解析条款' }));
  expect(await panel.findByText('保障范围与保险责任')).toBeTruthy();
  fireEvent.change(panel.getByLabelText('原件页码'), { target: { value: '2' } });
  expect(panel.getByText('此页需要 OCR，当前未能生成正文。')).toBeTruthy();

  fireEvent.click(panel.getByRole('button', { name: '解析说明书' }));
  expect(await panel.findByText('保障范围与保险责任')).toBeTruthy();
  expect(panel.getAllByRole('table')).toHaveLength(1);
  fireEvent.click(panel.getByRole('button', { name: '查看费率表解析结果' }));
  expect(panel.getByRole('table', { name: /第 2 页 · 表 1/ })).toBeTruthy();
  expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/documents/'))).toHaveLength(4);
});

it('同一时间只发一个解析请求，错误后可以重试，收起详情会结束等待', async () => {
  let respond!: (response: Response) => void;
  const { panel, fetchMock } = await openProduct(() => new Promise((resolve) => { respond = resolve; }));
  fireEvent.click(panel.getByRole('button', { name: '解析费率表' }));
  expect(panel.getByText('费率表正在解析')).toBeTruthy();
  fireEvent.click(panel.getByRole('button', { name: '解析现金价值表' }));
  expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/documents/'))).toHaveLength(1);
  await act(async () => respond(Response.json({
    message: '表格 PDF 解析失败', reason: 'compact_native_page_needs_ocr',
  }, { status: 422 })));
  expect(panel.getByText('PDF 含扫描页或未识别文字，数值表解析需要 OCR。')).toBeTruthy();
  fireEvent.click(panel.getByRole('button', { name: '重新解析费率表' }));
  const signal = fetchMock.mock.calls.at(-1)?.[1]?.signal;
  expect(signal?.aborted).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '收起' }));
  expect(signal?.aborted).toBe(true);
  expect(screen.queryByRole('region', { name: '资料解析' })).toBeNull();
});

it('一键解析只发一次批量请求，有失败仍可查看其余已保存的结果，重新打开后仍可读取', async () => {
  let saved = false;
  const { panel, fetchMock } = await openProduct((url, options) => {
    expect(url).toBe('/api/documents/11/saved-layout');
    expect(options?.method).toBe('GET');
    return Response.json(numeric);
  }, (options) => {
    if (options?.method === 'POST') {
      saved = true;
      return Response.json({ items: [
        { documentId: 11, state: 'saved', layoutStatus: 'completed_with_warnings' },
        { documentId: 12, state: 'failed', reason: 'invalid_pdf' },
      ] });
    }
    return Response.json({ items: saved ? [{ documentId: 11 }] : [] });
  });
  fireEvent.click(panel.getByRole('button', { name: '一键解析' }));
  expect(await panel.findByText('本次保存 1 份，复用 0 份，失败 1 份，1 份已保存结果需复核。')).toBeTruthy();
  expect(panel.getByText('解析失败 · invalid_pdf')).toBeTruthy();
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  fireEvent.click(panel.getByRole('button', { name: '查看费率表解析结果' }));
  expect(await panel.findByRole('table', { name: /第 2 页 · 表 1/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '收起' }));
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  fireEvent.click(await screen.findByRole('button', { name: '查看费率表解析结果' }));
  expect(await screen.findByRole('table', { name: /第 2 页 · 表 1/ })).toBeTruthy();
});
