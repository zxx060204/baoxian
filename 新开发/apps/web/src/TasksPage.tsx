import { Fragment, useEffect, useState } from 'react';

import { authHeaders, readError, subscribeTaskEvents } from './api';

interface Category {
  code: string;
  name: string;
}

interface IndexOverview extends Category {
  productCount: number;
  completedPages: number;
  latestCompletedAt: string | null;
}

interface TaskUnit {
  id: number;
  page: number;
  state: string;
  unitKey: string;
  attempts: number;
  lastError: string | null;
}

interface TaskProduct {
  id: number;
  name: string;
  companyName: string;
  salesStatus: string;
}

interface CrawlTask {
  id: number;
  type: string;
  status: string;
  scope: {
    category?: { code: string; name: string };
    company?: { id: number; name: string };
    products?: { id: number; name: string }[];
    companies?: { id: number; name: string }[];
    searchedPages?: string[];
    maxPages?: number;
    saleAfter?: string;
    saleDateStopReason?: 'cutoff';
  };
  createdAt: string;
  updatedAt: string;
  finishedAt?: string | null;
  readCount?: number;
  uniqueCount?: number;
  units?: TaskUnit[];
  products?: TaskProduct[];
  companySites?: { companyId: number; companyName: string; currentSiteUrl: string | null; state: string; reason: string | null }[];
}

interface TaskSnapshot {
  id: number;
  documentId: number;
  snapshotKind: string;
  pageTitle: string | null;
  fetchedAt: string;
}

interface DocumentFailure {
  id: number;
  taskId: number | null;
  companyName: string | null;
  productName: string | null;
  type: string;
  title: string;
  downloadUrl: string;
  sourcePageUrl: string;
  reason: string;
  message: string;
  createdAt: string;
}

const statusLabels: Record<string, string> = {
  queued: '排队',
  running: '运行中',
  completed: '完成',
  partial_failed: '部分失败',
  failed: '失败',
  cancelled: '已取消',
  pending: '待处理',
};

const unitStateLabels: Record<string, string> = {
  pending: '待处理',
  running: '运行中',
  completed: '完成',
  failed: '失败',
};

const typeLabels: Record<string, string> = {
  association_index: '索引录入',
  company_documents: '公司资料获取',
  company_site_discovery: '公司官网发现',
};

const failureReasonLabels: Record<string, string> = {
  network: '网络错误',
  http: 'HTTP 错误',
  timeout: '超时',
  cancelled: '已取消',
  format: '格式无效',
  empty: '空文件',
  too_large: '文件过大',
};
const materialLabels: Record<string, string> = { clause: '条款', rate_table: '费率表', cash_value_table: '现金价值表', prospectus: '产品说明书' };

import { formatBeijingTime as formatTime } from './time';

function scopeLabel(task: CrawlTask): string {
  if (task.scope.companies) {
    return task.scope.companies.map((company) => company.name).join('、');
  }

  if (task.scope.category) {
    return task.scope.category.name;
  }

  if (task.scope.company) {
    const names = (task.scope.products ?? [])
      .map((product) => product.name)
      .join('、');
    return `${task.scope.company.name}${names ? ` · ${names}` : ' · 全部产品'}`;
  }

  return '—';
}

export default function TasksPage({ token }: { token: string }) {
  const [overview, setOverview] = useState<IndexOverview[]>([]);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [overviewCategory, setOverviewCategory] = useState('');
  const [createCategory, setCreateCategory] = useState('');
  const [indexLimit, setIndexLimit] = useState('100');
  const [unlimited, setUnlimited] = useState(false);
  const [saleAfter, setSaleAfter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [appliedStatus, setAppliedStatus] = useState('');
  const [tasks, setTasks] = useState<CrawlTask[]>([]);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [detailPages, setDetailPages] = useState<Record<string, number>>({});
  const [detail, setDetail] = useState<CrawlTask | null>(null);
  const [failures, setFailures] = useState<DocumentFailure[]>([]);
  const [snapshots, setSnapshots] = useState<TaskSnapshot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadOverview() {
    if (!token) return;
    try {
      const response = await fetch('/api/index-overview', { headers: authHeaders(token) });
      if (!response.ok) throw new Error(await readError(response));
      const data = await response.json() as { items: IndexOverview[] };
      setOverview(data.items);
      setOverviewError(null);
    } catch (cause) {
      setOverviewError(cause instanceof Error ? cause.message : '加载索引总目录失败');
    }
  }

  useEffect(() => {
    void loadOverview();
    const timer = window.setInterval(() => void loadOverview(), 10000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function loadTasks() {
    if (!token) {
      setError('请先在页面顶部输入内部访问令牌');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const query = appliedStatus ? `?status=${appliedStatus}` : '';
      const response = await fetch(`/api/crawl-tasks${query}`, {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const data = (await response.json()) as { items: CrawlTask[] };
      setTasks(data.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载任务失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadTasks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, appliedStatus]);

  async function createTask(category: string) {
    setError(null);
    setNotice(null);

    if (!category) {
      setError('请选择索引类别');
      return;
    }

    const count = Number(indexLimit);
    if (!unlimited && (!Number.isInteger(count) || count < 10 || count > 10000 || count % 10 !== 0)) {
      setError('获取索引数量须为 10 至 10000 之间的 10 的倍数');
      return;
    }

    setBusy(true);

    try {
      const response = await fetch('/api/crawl-tasks', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({ type: 'association_index', category, ...(!unlimited ? { maxPages: count / 10 } : {}), ...(saleAfter ? { saleAfter } : {}) }),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const created = (await response.json()) as CrawlTask;
      setNotice(`索引录入任务 #${created.id} 已排队`);
      setOverviewCategory(category);
      setCreateCategory('');
      setDetail(created);
      await loadTasks();
      await loadOverview();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建任务失败');
    } finally {
      setBusy(false);
    }
  }

  async function showDetail(id: number) {
    setError(null);
    setDetailPages({});

    try {
      const [response, failureResponse] = await Promise.all([
        fetch(`/api/crawl-tasks/${id}`, {
          headers: authHeaders(token),
        }),
        fetch(`/api/reports/failures?taskId=${id}`, {
          headers: authHeaders(token),
        }),
      ]);

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const task = (await response.json()) as CrawlTask;
      setDetail(task);
      setSnapshots([]);
      if (task.type === 'company_documents') {
        const snapshotResponse = await fetch(`/api/crawl-tasks/${id}/document-snapshots`, { headers: authHeaders(token) });
        if (!snapshotResponse.ok) setError(`加载任务快照失败：${await readError(snapshotResponse)}`);
        else setSnapshots(((await snapshotResponse.json()) as { items: TaskSnapshot[] }).items);
      }
      if (!failureResponse.ok) setError(`加载失败详情失败：${await readError(failureResponse)}`);
      setFailures(
        failureResponse.ok
          ? ((await failureResponse.json()) as { items: DocumentFailure[] })
              .items
          : [],
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载任务详情失败');
    }
  }

  async function previewSnapshot(id: number) {
    try {
      const response = await fetch(`/api/document-sources/${id}/snapshot`, { headers: authHeaders(token) });
      if (!response.ok) throw new Error(await readError(response));
      const url = URL.createObjectURL(await response.blob());
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '查看快照失败'); }
  }

  async function controlTask(id: number, action: 'cancel' | 'retry') {
    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const response = await fetch(`/api/crawl-tasks/${id}/${action}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: '{}',
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const updated = (await response.json()) as CrawlTask;
      setDetail(updated);
      setNotice(action === 'cancel' ? '任务已取消' : '任务已重新排队');
      await loadTasks();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '任务操作失败');
    } finally {
      setBusy(false);
    }
  }

  const detailId = detail?.id;

  useEffect(() => {
    if (!detailId || !token) {
      return;
    }

    const controller = new AbortController();

    void subscribeTaskEvents(
      detailId,
      token,
      (event) => {
        const updated = event as unknown as CrawlTask;
        setDetail((current) => current?.id === updated.id ? { ...current, ...updated, products: updated.products ?? current.products, companySites: updated.companySites ?? current.companySites } : current);

        if (
          ['completed', 'failed', 'partial_failed', 'cancelled'].includes(
            updated.status,
          )
        ) {
          void loadTasks();
          if (updated.type === 'company_site_discovery') {
            void fetch(`/api/crawl-tasks/${detailId}`, { headers: authHeaders(token), signal: controller.signal })
              .then(response => response.ok ? response.json() : null)
              .then((fresh: CrawlTask | null) => { if (fresh) setDetail(current => current?.id === detailId ? { ...current, ...fresh } : current); })
              .catch(() => undefined);
          }
        }
      },
      controller.signal,
    ).catch(() => {
      // 流断开后仍可通过刷新恢复状态
    });

    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailId, token]);

  const indexTasks = tasks.filter((task) => task.type === 'association_index' && (!overviewCategory || task.scope.category?.code === overviewCategory));
  const companyTasks = tasks.filter(
    (task) => task.type === 'company_documents',
  );
  const siteTasks = tasks.filter(
    (task) => task.type === 'company_site_discovery',
  );

  function renderDetail(task: CrawlTask) {
    function paginate<T>(name: string, rows: T[]): T[] {
      const page = Math.min(detailPages[name] ?? 0, Math.max(0, Math.ceil(rows.length / 10) - 1));
      return rows.slice(page * 10, page * 10 + 10);
    }
    function detailNavigation(name: string, count: number) {
      const page = Math.min(detailPages[name] ?? 0, Math.max(0, Math.ceil(count / 10) - 1));
      return <div className="actions" aria-label={`${name}分页`}>
        <button type="button" disabled={page === 0} onClick={() => setDetailPages({ ...detailPages, [name]: page - 1 })}>上一页</button>
        <span>第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(count / 10))} 页（{count} 条）</span>
        <button type="button" disabled={(page + 1) * 10 >= count} onClick={() => setDetailPages({ ...detailPages, [name]: page + 1 })}>下一页</button>
      </div>;
    }
    return (
      <div className="detail">
        <p className="muted">
          {typeLabels[task.type] ?? task.type} · {scopeLabel(task)} · 创建{' '}
          {formatTime(task.createdAt)}
          {task.finishedAt ? ` · 结束 ${formatTime(task.finishedAt)}` : ''}
          {typeof task.readCount === 'number'
            ? ` · 读取 ${task.readCount}`
            : ''}
          {task.type === 'association_index' ? ` · 获取范围：${task.scope.maxPages ? `最多 ${task.scope.maxPages * 10} 条` : '不限数量'}` : ''}
          {task.scope.saleAfter ? ` · 销售时间晚于 ${task.scope.saleAfter}（不含当日）` : ''}
          {task.scope.saleDateStopReason === 'cutoff' ? ' · 已到销售时间边界' : ''}
          {typeof task.uniqueCount === 'number'
            ? ` · 去重 ${task.uniqueCount}`
            : ''}
        </p>

        {(task.products ?? []).length > 0 && (
          <>
            <h4>本次录入产品（{task.products?.length}）</h4>
            <table>
              <thead>
                <tr>
                  <th>产品</th>
                  <th>公司</th>
                  <th>销售状态</th>
                </tr>
              </thead>
              <tbody>
                {paginate('本次录入产品', task.products ?? []).map((product) => (
                  <tr key={product.id}>
                    <td>
                      #{product.id} {product.name}
                    </td>
                    <td>{product.companyName}</td>
                    <td>
                      {product.salesStatus === 'on_sale'
                        ? '在售'
                        : product.salesStatus === 'off_sale'
                          ? '停售'
                          : product.salesStatus === 'disabled'
                            ? '停用'
                          : '未知'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {detailNavigation('本次录入产品', task.products?.length ?? 0)}
          </>
        )}

        {task.type === 'association_index' &&
          (task.products ?? []).length === 0 && (
            <p className="muted">该任务没有产品明细记录</p>
          )}

        {(task.scope.searchedPages ?? []).length > 0 && (
          <div className="muted">
            已搜索页面：
            <ul>
              {(task.scope.searchedPages ?? []).map((page) => (
                <li key={page}>
                  <a href={page} target="_blank" rel="noreferrer">
                    {page}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {task.type === 'company_site_discovery' ? <>
        <h4>公司官网发现结果</h4>
        <table><thead><tr><th>公司</th><th>处理状态</th><th>当前官网</th><th>说明</th></tr></thead><tbody>
          {paginate('公司发现结果', task.companySites ?? (task.scope.companies ?? []).map(company => {
            const unit = task.units?.find(item => item.unitKey === `company-site:${company.id}`);
            return { companyId: company.id, companyName: company.name, currentSiteUrl: null, state: unit?.state ?? 'pending', reason: unit?.lastError ?? null };
          })).map(site => <tr key={site.companyId}><td>{site.companyName}</td><td>{unitStateLabels[site.state] ?? site.state}</td><td>{site.currentSiteUrl ? <a href={site.currentSiteUrl} target="_blank" rel="noreferrer">{site.currentSiteUrl}</a> : '尚未设置'}</td><td>{site.reason ?? '—'}</td></tr>)}
        </tbody></table>
        {detailNavigation('公司发现结果', task.companySites?.length ?? task.scope.companies?.length ?? 0)}
        </> : <>
        <h4>处理情况</h4>
        <table>
          <thead>
            <tr>
              <th>处理对象</th>
              <th>处理状态</th>
              <th>尝试</th>
              <th>最后错误</th>
            </tr>
          </thead>
          <tbody>
            {(task.units ?? []).length === 0 ? (
              <tr>
                <td colSpan={4} className="muted">
                  没有处理单元
                </td>
              </tr>
            ) : (
              paginate('处理单元', task.units ?? []).map((unit) => (
                <tr key={unit.id}>
                  <td>{task.type === 'association_index' ? `第 ${unit.page} 页` : task.scope.products?.find(product => unit.unitKey.endsWith(`:product:${product.id}`))?.name ?? task.scope.company?.name ?? '全部产品'}</td>
                  <td>{unitStateLabels[unit.state] ?? unit.state}</td>
                  <td>{unit.attempts}</td>
                  <td>{unit.lastError ?? '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {detailNavigation('处理单元', task.units?.length ?? 0)}
        </>}

        {task.type === 'company_documents' && <>
          <h4>本次来源快照（{snapshots.length}）</h4>
          {snapshots.length === 0 ? <p className="muted">本次没有快照记录</p> : <table><thead><tr><th>资料</th><th>来源内容</th><th>获取时间</th><th /></tr></thead><tbody>
            {paginate('来源快照', snapshots).map(snapshot => <tr key={snapshot.id}>
              <td>{snapshot.pageTitle ?? `文件 #${snapshot.documentId}`}</td>
              <td>{{ webpage: '网页', api_response: '接口响应', combined: '组合页面', unknown: '类型待确认' }[snapshot.snapshotKind] ?? '类型待确认'}</td>
              <td>{formatTime(snapshot.fetchedAt)}</td>
              <td><button type="button" className="secondary" onClick={() => void previewSnapshot(snapshot.id)}>查看快照</button></td>
            </tr>)}
          </tbody></table>}
          {detailNavigation('来源快照', snapshots.length)}
        </>}
        <h4>失败详情（{failures.length}）</h4>
        <table>
          <thead>
            <tr>
              <th>公司 / 产品</th>
              <th>类型</th>
              <th>标题</th>
              <th>原因</th>
              <th>来源页面</th>
              <th>下载地址</th>
            </tr>
          </thead>
          <tbody>
            {failures.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  没有失败记录
                </td>
              </tr>
            ) : (
              paginate('失败详情', failures).map((failure) => (
                <tr key={failure.id}>
                  <td>
                    {failure.companyName ?? '—'} /{' '}
                    {failure.productName ?? '—'}
                  </td>
                  <td>{materialLabels[failure.type] ?? '其他资料'}</td>
                  <td>{failure.title}</td>
                  <td>
                    <div>
                      {failureReasonLabels[failure.reason] ?? failure.reason}
                    </div>
                    <div className="muted">{failure.message}</div>
                  </td>
                  <td>
                    <a
                      href={failure.sourcePageUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开来源页
                    </a>
                  </td>
                  <td>
                    <a
                      href={failure.downloadUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开下载地址
                    </a>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {detailNavigation('失败详情', failures.length)}
      </div>
    );
  }

  function renderTaskTable(title: string, items: CrawlTask[]) {
    const page = Math.min(pages[title] ?? 0, Math.max(0, Math.ceil(items.length / 10) - 1));
    return (
      <>
        <h3>{title}</h3>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>{title === '索引录入' ? '索引类别' : '公司 / 产品'}</th>
              <th>任务状态</th>
              <th>创建时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  还没有{title}任务
                </td>
              </tr>
            ) : (
              items.slice(page * 10, page * 10 + 10).map((task) => (
                <Fragment key={task.id}>
                  <tr>
                    <td>{task.id}</td>
                    <td>{scopeLabel(task)}</td>
                    <td>
                      <span className={`badge ${task.status}`}>
                        {statusLabels[task.status] ?? task.status}
                      </span>
                    </td>
                    <td>{formatTime(task.createdAt)}</td>
                    <td>
                      <div className="actions">
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => detail?.id === task.id ? (setDetail(null), setFailures([])) : void showDetail(task.id)}
                        >
                          {detail?.id === task.id ? '收起' : '查看'}
                        </button>
                        {['queued', 'running'].includes(task.status) && (
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => void controlTask(task.id, 'cancel')}
                          >
                            取消
                          </button>
                        )}
                        {['failed', 'partial_failed', 'cancelled'].includes(
                          task.status,
                        ) && (
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => void controlTask(task.id, 'retry')}
                          >
                            重试
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {detail?.id === task.id && (
                    <tr>
                      <td colSpan={5}>{renderDetail(detail)}</td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
        <div className="actions" aria-label={`${title}分页`}>
          <button type="button" disabled={page === 0} onClick={() => setPages({ ...pages, [title]: page - 1 })}>上一页</button>
          <span>第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(items.length / 10))} 页（{items.length} 条）</span>
          <button type="button" disabled={(page + 1) * 10 >= items.length} onClick={() => setPages({ ...pages, [title]: page + 1 })}>下一页</button>
        </div>
      </>
    );
  }

  return (
    <section>
      <h2>采集任务</h2>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}

      <div className="task-form">
        <label>
          状态筛选
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">全部</option>
            {Object.entries(statusLabels)
              .filter(([value]) => value !== 'pending')
              .map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
          </select>
        </label>

        <button type="button" className="secondary" onClick={() => { setPages({}); setDetail(null); if (appliedStatus === statusFilter) void loadTasks(); else setAppliedStatus(statusFilter); }}>查询</button>
        <button
          type="button"
          className="secondary"
          onClick={() => { void loadTasks(); void loadOverview(); }}
        >
          刷新
        </button>
      </div>

      {busy && <p className="muted">处理中…</p>}

      <section aria-label="索引录入总目录">
        <h3>索引录入总目录</h3>
        {overviewError && <p role="alert">总目录加载失败：{overviewError}</p>}
        <table>
          <thead><tr><th>索引类别</th><th>已录入页</th><th>本地产品数</th><th>最新完成时间</th><th>任务</th><th>创建录入任务</th></tr></thead>
          <tbody>{overview.map((item) => <Fragment key={item.code}>
            <tr>
              <td>{item.name}</td><td>{item.completedPages}</td><td>{item.productCount}</td>
              <td>{item.latestCompletedAt ? formatTime(item.latestCompletedAt) : '—'}</td>
              <td><button type="button" aria-label={`${overviewCategory === item.code ? '收起' : '查看'}${item.name}任务`} onClick={() => { setOverviewCategory(overviewCategory === item.code ? '' : item.code); setPages({}); setDetail(null); }}>{overviewCategory === item.code ? '收起任务' : '查看任务'}</button></td>
              <td><button type="button" disabled={busy} aria-label={`创建${item.name}录入任务`} onClick={() => { setCreateCategory(item.code); setIndexLimit('100'); setUnlimited(false); setSaleAfter(''); }}>创建任务</button></td>
            </tr>
            {createCategory === item.code && <tr><td colSpan={6}>
              <form className="expanded-panel create-index-form" onSubmit={(event) => { event.preventDefault(); void createTask(item.code); }}>
                <h4>创建{item.name}录入任务</h4>
                <div className="create-index-fields">
                  <label>获取索引数量 <input type="number" min="10" max="10000" step="10" value={indexLimit} disabled={unlimited} onChange={(event) => setIndexLimit(event.target.value)} /></label>
                  <label className="inline-check"><input type="checkbox" checked={unlimited} onChange={(event) => setUnlimited(event.target.checked)} />不限数量</label>
                  <label>销售时间晚于 <input type="date" value={saleAfter} onChange={(event) => setSaleAfter(event.target.value)} /></label>
                </div>
                <div className="actions"><button type="submit" disabled={busy}>提交录入任务</button>
                <button type="button" className="secondary" onClick={() => setCreateCategory('')}>取消创建</button></div>
              </form>
            </td></tr>}
            {overviewCategory === item.code && <tr><td colSpan={6}><div className="expanded-panel">{renderTaskTable('索引录入', indexTasks)}</div></td></tr>}
          </Fragment>)}</tbody>
        </table>
      </section>

      {renderTaskTable('公司资料获取', companyTasks)}
      {renderTaskTable('公司官网发现', siteTasks)}
    </section>
  );
}
