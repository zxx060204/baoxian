// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import TasksPage from '../src/TasksPage';
import ProductsPage from '../src/ProductsPage';
import ReportsPage from '../src/ReportsPage';
import ReviewPage from '../src/ReviewPage';
import CompaniesPage from '../src/CompaniesPage';

vi.mock('../src/api', () => ({
  authHeaders: () => ({}),
  readError: async () => '请求失败',
  subscribeTaskEvents: async () => undefined,
}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('中台列表交互', () => {
  it('产品筛选在点击查询后生效，资料类型可选全部', async () => {
    const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => ({ items: url === '/api/companies' ? [] : [{ id: 1, name: '测试产品', companyName: '公司', salesStatus: 'on_sale', categoryName: '人寿', integrationStatus: 'not_started' }] }) }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProductsPage token="test" />);
    await screen.findByText('测试产品');
    const before = fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/products?')).length;
    fireEvent.change(screen.getByLabelText('产品名称'), { target: { value: '年金' } });
    fireEvent.change(screen.getByLabelText('资料类型'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('资料状态'), { target: { value: 'download_failed' } });
    expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/products?'))).toHaveLength(before);
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/products?'))).toHaveLength(before + 1));
    const url = fetchMock.mock.calls.filter(([value]) => value.startsWith('/api/products?')).at(-1)![0];
    const query = new URLSearchParams(url.split('?')[1]);
    expect(query.get('q')).toBe('年金');
    expect(query.get('materialStatus')).toBe('download_failed');
    expect(query.has('materialType')).toBe(false);
  });
  it('任务状态筛选在点击查询后才更新', async () => {
    const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => ({ items: url === '/api/index-overview' ? [] : [{ id: 31, type: 'company_documents', status: 'completed', scope: { company: { id: 1, name: '测试公司' } }, createdAt: '2026-11-10T00:00:00Z' }] }) }));
    vi.stubGlobal('fetch', fetchMock);
    render(<TasksPage token="test" />);
    await screen.findByText('测试公司 · 全部产品');
    const before = fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/crawl-tasks')).length;
    fireEvent.change(screen.getByLabelText('状态筛选'), { target: { value: 'failed' } });
    expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/crawl-tasks'))).toHaveLength(before);
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/crawl-tasks?status=failed')).toBe(true));
    expect(screen.getAllByRole('columnheader', { name: '任务状态' }).length).toBeGreaterThan(0);
  });
  it('公司资料任务展示本次快照和内容类型', async () => {
    const task = { id: 31, type: 'company_documents', status: 'completed', scope: { company: { id: 1, name: '测试公司' } }, createdAt: '2026-11-10T00:00:00Z', units: [], products: [] };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/crawl-tasks/31') return { ok: true, json: async () => task };
      if (url === '/api/crawl-tasks/31/document-snapshots') return { ok: true, json: async () => ({ items: [{ id: 9, documentId: 4, pageTitle: '测试条款', snapshotKind: 'api_response', fetchedAt: '2026-11-10T00:00:00Z' }] }) };
      if (url === '/api/crawl-tasks') return { ok: true, json: async () => ({ items: [task] }) };
      return { ok: true, json: async () => ({ items: [] }) };
    }));
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '查看' }));
    expect(await screen.findByText('测试条款')).toBeTruthy();
    expect(screen.getByText('接口响应')).toBeTruthy();
  });

  it('创建索引任务时指定销售时间下限', async () => {
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/index-overview') return { ok: true, json: async () => ({ items: [{ code: 'health', name: '健康保险', completedPages: 0, productCount: 0, latestCompletedAt: null }] }) };
      if (options?.method === 'POST') return { ok: true, json: async () => ({ id: 104, type: 'association_index', status: 'queued', scope: { category: { code: 'health', name: '健康保险' } }, createdAt: '2026-11-10T00:00:00Z' }) };
      return { ok: true, json: async () => ({ items: [] }) };
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '创建健康保险录入任务' }));
    fireEvent.change(screen.getByLabelText('销售时间晚于'), { target: { value: '2026-01-01' } });
    fireEvent.click(screen.getByRole('button', { name: '提交录入任务' }));
    expect(await screen.findByText('索引录入任务 #104 已排队')).toBeTruthy();
    expect(fetchMock.mock.calls.find(call => call[1]?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ type: 'association_index', category: 'health', maxPages: 10, saleAfter: '2026-01-01' }));
  });
  it('在类别行创建该类别的录入任务并展开任务列表', async () => {
    const created = { id: 94, type: 'association_index', status: 'queued', scope: { category: { code: 'health', name: '健康保险' } }, createdAt: '2026-11-10T00:00:00Z' };
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/index-overview') return { ok: true, json: async () => ({ items: [{ code: 'health', name: '健康保险', completedPages: 175, productCount: 1750, latestCompletedAt: null }] }) };
      if (options?.method === 'POST') return { ok: true, json: async () => created };
      return { ok: true, json: async () => ({ items: fetchMock.mock.calls.some((call) => call[1]?.method === 'POST') ? [created] : [] }) };
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '创建健康保险录入任务' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: '获取索引数量' }), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: '提交录入任务' }));
    expect(await screen.findByText('索引录入任务 #94 已排队')).toBeTruthy();
    expect(fetchMock.mock.calls.find((call) => call[1]?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ type: 'association_index', category: 'health', maxPages: 2 }));
    expect(screen.getByRole('button', { name: '收起健康保险任务' })).toBeTruthy();
  });
  it('可以选择不限数量，也可以取消创建', async () => {
    const created = { id: 95, type: 'association_index', status: 'queued', scope: { category: { code: 'health', name: '健康保险' } }, createdAt: '2026-11-10T00:00:00Z' };
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/index-overview') return { ok: true, json: async () => ({ items: [{ code: 'health', name: '健康保险', completedPages: 0, productCount: 0, latestCompletedAt: null }] }) };
      if (options?.method === 'POST') return { ok: true, json: async () => created };
      return { ok: true, json: async () => ({ items: [] }) };
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '创建健康保险录入任务' }));
    fireEvent.click(screen.getByRole('button', { name: '取消创建' }));
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === 'POST')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '创建健康保险录入任务' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '不限数量' }));
    expect((screen.getByRole('spinbutton', { name: '获取索引数量' }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '提交录入任务' }));
    expect(await screen.findByText('索引录入任务 #95 已排队')).toBeTruthy();
    expect(fetchMock.mock.calls.find((call) => call[1]?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ type: 'association_index', category: 'health' }));
  });
  it('索引总目录在对应行内展开和收起类别任务', async () => {
    const tasks = [
      { id: 92, type: 'association_index', status: 'cancelled', scope: { category: { code: 'life', name: '人寿保险' } }, createdAt: '2026-11-10T00:00:00Z' },
      { id: 93, type: 'association_index', status: 'completed', scope: { category: { code: 'health', name: '健康保险' } }, createdAt: '2026-11-10T00:00:00Z' },
    ];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/index-overview') return { ok: true, json: async () => ({ items: [
        { code: 'life', name: '人寿保险', status: 'incomplete', productCount: 1280, cycle: 1, completedPages: 128, nextPage: 129, reportedTotal: 2000, completedAt: null },
        { code: 'health', name: '健康保险', status: 'not_started', productCount: 0, cycle: 1, completedPages: 0, nextPage: 1, reportedTotal: null, completedAt: null },
      ] }) };
      if (url === '/api/categories' || url.startsWith('/api/reports/failures')) return { ok: true, json: async () => ({ items: [] }) };
      return { ok: true, json: async () => ({ items: tasks }) };
    }));
    render(<TasksPage token="test" />);
    const directory = await screen.findByRole('region', { name: '索引录入总目录' });
    expect(within(directory).getByText('1280')).toBeTruthy();
    expect(within(directory).getByText('128')).toBeTruthy();
    expect(screen.queryByText('92')).toBeNull();
    fireEvent.click(within(directory).getByRole('button', { name: '查看人寿保险任务' }));
    expect(within(directory).getByText('92')).toBeTruthy();
    expect(screen.queryByText('93')).toBeNull();
    fireEvent.click(within(directory).getByRole('button', { name: '收起人寿保险任务' }));
    expect(screen.queryByText('92')).toBeNull();
  });
  it('公司目录每页十条且保留公司 ID', async () => {
    const items = Array.from({ length: 11 }, (_, i) => ({ id: 100 + i, name: `企业${i + 1}`, officialSiteUrl: null, documentEntryUrl: null, notes: null }));
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ items }) })));
    render(<CompaniesPage token="test" />);
    expect(await screen.findByText('企业1')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'ID' })).toBeTruthy();
    expect(screen.queryByText('企业11')).toBeNull();
    fireEvent.click(within(screen.getByLabelText('公司分页')).getByRole('button', { name: '下一页' }));
    expect(screen.getByText('企业11')).toBeTruthy();
  });
  it('一键为所有未设置官网的公司创建发现任务，不受公司分页限制', async () => {
    const items = Array.from({ length: 11 }, (_, i) => ({ id: 100 + i, name: `企业${i + 1}`, officialSiteUrl: i === 0 ? 'https://example.com' : null, documentEntryUrl: null, notes: null }));
    const fetchMock = vi.fn(async (_url: string, options?: RequestInit) => options?.method === 'POST'
      ? { ok: true, json: async () => ({ id: 101, scope: { companies: items.slice(1) } }) }
      : { ok: true, json: async () => ({ items }) });
    vi.stubGlobal('fetch', fetchMock);
    render(<CompaniesPage token="test" />);
    expect(await screen.findByText('企业1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '一键发现官网' }));
    expect(await screen.findByText('官网发现任务 #101 已排队，共 10 家公司')).toBeTruthy();
    expect(fetchMock.mock.calls.find(call => call[1]?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ type: 'company_site_discovery' }));
  });
  it('任务详情展示实际处理单元，可再次点击收回，每类列表每页十条', async () => {
    const tasks = Array.from({ length: 11 }, (_, i) => ({ id: i + 1, type: 'association_index', status: 'completed', scope: { category: { code: '00', name: '人寿' } }, createdAt: '2026-11-10T00:00:00Z' }));
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/index-overview') return { ok: true, json: async () => ({ items: [{ code: '00', name: '人寿', completedPages: 2, productCount: 11, latestCompletedAt: null }] }) };
      if (url.startsWith('/api/reports/failures')) return { ok: true, json: async () => ({ items: [] }) };
      if (url === '/api/crawl-tasks/1') return { ok: true, json: async () => ({ ...tasks[0], units: Array.from({ length: 11 }, (_, i) => ({ id: i + 1, page: i + 1, state: 'completed', unitKey: i ? `单元${i + 1}` : '真实处理单元', attempts: 1, lastError: null })) }) };
      return { ok: true, json: async () => ({ items: tasks }) };
    }));
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '查看人寿任务' }));
    const button = (await screen.findAllByRole('button', { name: '查看' }))[0]!;
    expect(screen.getAllByRole('button', { name: '查看' })).toHaveLength(10);
    fireEvent.click(button);
    expect(await screen.findByText('第 1 页')).toBeTruthy();
    expect(screen.queryByText('真实处理单元')).toBeNull();
    expect(screen.queryByText('第 11 页')).toBeNull();
    fireEvent.click(within(screen.getByLabelText('处理单元分页')).getByRole('button', { name: '下一页' }));
    expect(screen.getByText('第 11 页')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '收起' }));
    expect(screen.queryByText('第 11 页')).toBeNull();
    const navigation = screen.getByLabelText('索引录入分页');
    fireEvent.click(within(navigation).getByRole('button', { name: '下一页' }));
    expect(screen.getAllByRole('button', { name: '查看' })).toHaveLength(1);
  });

  it('产品列表每页十条，产品详情可收回', async () => {
    const product = { id: 1, name: '测试产品', companyName: '测试公司', salesStatus: 'disabled', saleDateRaw: '2019-01-02', sameNameCount: 1, identityGroup: 'individual', categoryName: '人寿', integrationStatus: 'completed', sourceUrl: 'https://example.test', materials: [], sources: [], history: [], associationRecords: [], statusFetchedAt: '2026-11-10T00:00:00Z' };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/companies') return { ok: true, json: async () => ({ items: [] }) };
      if (url === '/api/products/1') return { ok: true, json: async () => product };
      const params = new URLSearchParams(url.split('?')[1]);
      const items = params.get('offset') === '10' ? [{ ...product, id: 11, name: '第十一项' }] : Array.from({ length: Number(params.get('limit')) }, (_, i) => ({ ...product, id: i + 1, name: i ? `产品${i + 1}` : product.name }));
      return { ok: true, json: async () => ({ items }) };
    }));
    render(<ProductsPage token="test" />);
    expect(screen.queryByRole('button', { name: '查看已接入产品' })).toBeNull();
    expect(await screen.findAllByRole('button', { name: '详情' })).toHaveLength(10);
    fireEvent.click(screen.getAllByRole('button', { name: '详情' })[0]!);
    expect(await screen.findByText(/销售时间（来源）：2019-01-02/)).toBeTruthy();
    expect(screen.getByText(/销售状态：停用/)).toBeTruthy();
    expect(await screen.findByText('确认产品名称')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '收起' }));
    expect(screen.queryByText('确认产品名称')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('第十一项')).toBeTruthy();
  });

  it('官网发现详情显示公司结果与当前官网，不展示内部单元名', async () => {
    const task = { id: 91, type: 'company_site_discovery', status: 'completed', scope: { category: { code: 'company_site', name: '公司官网发现' }, companies: [{ id: 6, name: '测试人寿' }] }, createdAt: '2026-11-10T00:00:00Z' };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/categories' || url.startsWith('/api/reports/failures')) return { ok: true, json: async () => ({ items: [] }) };
      if (url === '/api/crawl-tasks/91') return { ok: true, json: async () => ({ ...task, units: [{ id: 3, page: 1, state: 'completed', unitKey: 'company-site:6', attempts: 0, lastError: null }], companySites: [{ companyId: 6, companyName: '测试人寿', currentSiteUrl: 'https://insurer.example', state: 'completed', reason: null }] }) };
      return { ok: true, json: async () => ({ items: [task] }) };
    }));
    render(<TasksPage token="test" />);
    fireEvent.click(await screen.findByRole('button', { name: '查看' }));
    expect(await screen.findByRole('link', { name: 'https://insurer.example' })).toBeTruthy();
    expect(screen.queryByText('company-site:6')).toBeNull();
  });

  it('官网待确认显示未通过验证的候选并要求人工明确选择', async () => {
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => ({ ok: true, json: async () => url.startsWith('/api/review-items') && !options?.method ? { items: [{ id: 7, type: 'company_site', payload: { companyName: '测试人寿', reason: '未能验证官网', siteCandidates: [{ url: 'https://candidate.example', title: '候选网站', score: 0, verified: false }] } }] } : { items: [] } }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ReviewPage token="test" />);
    expect(await screen.findByText('未能验证官网')).toBeTruthy();
    expect(screen.getByText(/未通过自动验证/)).toBeTruthy();
    const confirm = screen.getByRole('button', { name: '设为官网' }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '选择候选 https://candidate.example' }));
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await screen.findByText('已设为官网：https://candidate.example');
    expect(fetchMock.mock.calls.find((call) => call[1]?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ action: 'select_site', siteUrl: 'https://candidate.example' }));
  });


  it('两种报告分别每页展示十条', async () => {
    const coverage = Array.from({ length: 11 }, (_, i) => ({ taskId: i + 1, categoryCode: '00', categoryName: '人寿', status: 'completed', pageFrom: 1, pageTo: 1, reportedTotal: 1, readCount: 1, uniqueCount: 1, duplicateCount: 0, failedUnits: 0, failures: [] }));
    const companies = Array.from({ length: 11 }, (_, i) => ({ companyId: i + 1, companyName: `公司${i + 1}`, products: 1, documents: 1, links: 1, failures: {}, materials: {} }));
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('coverage') ? { items: coverage } : { companies, totals: { products: 11, documents: 11, links: 11, failures: 0 } } })));
    render(<ReportsPage token="test" />);
    expect(await screen.findByText('#1')).toBeTruthy();
    expect(screen.queryByText('#11')).toBeNull();
    expect(screen.queryByText('公司11')).toBeNull();
    fireEvent.click(within(screen.getByLabelText('索引报告分页')).getByRole('button', { name: '下一页' }));
    fireEvent.click(within(screen.getByLabelText('公司资料报告分页')).getByRole('button', { name: '下一页' }));
    expect(screen.getByText('#11')).toBeTruthy();
    expect(screen.getByText('公司11')).toBeTruthy();
  });
});
