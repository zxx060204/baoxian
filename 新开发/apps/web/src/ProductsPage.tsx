import { Fragment, useEffect, useState } from 'react';

import { authHeaders, readError } from './api';
import ProductDetailPanel from './ProductDetailPanel';

interface Product {
  id: number;
  companyName: string;
  name: string;
  sameNameCount: number;
  identityGroup: string;
  salesStatus: string;
  salesStatusRaw: string | null;
  saleDateRaw: string | null;
  categoryName: string;
  sourceUrl: string;
  officialProductUrl: string | null;
  officialProductCode: string | null;
  filingNumber: string | null;
  confirmedName: string | null;
  confirmedSalesStatus: string | null;
  correctionNote: string | null;
  confirmedAt: string | null;
  confirmedBy: string | null;
  integrationStatus: string;
  integrationStatusReason: string | null;
  integrationStatusUpdatedAt: string | null;
}

interface ProductMaterial {
  type: string;
  label: string;
  status: string;
}

interface ProductDetail extends Product {
  statusFetchedAt: string;
  materials: ProductMaterial[];
  associationRecords: {
    associationRecordId: string | null;
    salesStatus: string;
    salesStatusRaw: string | null;
    saleDateRaw: string | null;
    statusFetchedAt: string;
    sourceUrl: string;
    categoryName: string;
    officialProductCode: string | null;
    filingNumber: string | null;
  }[];
  sources: ProductSource[];
  history: ProductSource[];
}

interface ProductSource {
  id: number;
  documentId: number;
  type: string;
  mimeType?: string;
  sourceUrl: string;
  downloadUrl: string;
  snapshotKey: string;
  fetchedAt: string;
  linkStatus: string;
}

const statusLabels: Record<string, string> = {
  on_sale: '在售',
  off_sale: '停售',
  disabled: '停用',
  stopped: '停售/停用组',
  unknown: '未知',
};

const materialStatusLabels: Record<string, string> = {
  not_run: '尚未执行',
  not_found: '未找到',
  downloaded: '已下载',
  download_failed: '下载失败',
  attribution_pending: '归属待确认',
};

const integrationStatusLabels: Record<string, string> = {
  not_started: '尚未接入',
  running: '接入中',
  completed: '已接入',
  partial_failed: '部分接入',
  failed: '接入失败',
  not_found: '未找到资料',
};

export default function ProductsPage({ token }: { token: string }) {
  const [items, setItems] = useState<Product[]>([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [query, setQuery] = useState('');
  const [companyId, setCompanyId] = useState('');
  const [companies, setCompanies] = useState<{ id: number; name: string }[]>([]);
  const [integrationFilter, setIntegrationFilter] = useState('');
  const [materialType, setMaterialType] = useState('');
  const [materialStatus, setMaterialStatus] = useState('');
  const [applied, setApplied] = useState({ statusFilter: '', query: '', companyId: '', integrationFilter: '', materialType: '', materialStatus: '' });
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [editName, setEditName] = useState('');
  const [editStatus, setEditStatus] = useState('');
  const [editNote, setEditNote] = useState('');
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    if (!token) {
      setError('请先在页面顶部输入内部访问令牌');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const params = new URLSearchParams({ limit: '11', offset: String(page * 10) });
      if (applied.statusFilter) params.set('status', applied.statusFilter);
      if (applied.query.trim()) params.set('q', applied.query.trim());
      if (applied.companyId) params.set('companyId', applied.companyId);
      if (applied.integrationFilter) params.set('integrationStatus', applied.integrationFilter);
      if (applied.materialStatus) { if (applied.materialType) params.set('materialType', applied.materialType); params.set('materialStatus', applied.materialStatus); }
      const response = await fetch(`/api/products?${params}`, {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const data = (await response.json()) as { items: Product[] };
      setHasMore(data.items.length > 10);
      setItems(data.items.slice(0, 10));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载产品失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, applied, page]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    void fetch('/api/companies', { headers: authHeaders(token), signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error('公司列表加载失败')))
      .then((data: { items: { id: number; name: string }[] }) => setCompanies(data.items))
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '公司列表加载失败'); });
    return () => controller.abort();
  }, [token]);

  async function showDetail(id: number) {
    setDetailError(null);

    try {
      const response = await fetch(`/api/products/${id}`, {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setDetailError(await readError(response));
        return;
      }

      const data = (await response.json()) as ProductDetail;
      setDetail(data);
      setEditName(data.confirmedName ?? data.name);
      setEditStatus(data.confirmedSalesStatus ?? data.salesStatus);
      setEditNote(data.correctionNote ?? '');
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : '加载详情失败');
    }
  }

  async function saveCorrection() {
    if (!detail) return;
    try {
      const response = await fetch(`/api/products/${detail.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders(token) }, body: JSON.stringify({ name: editName, salesStatus: editStatus, correctionNote: editNote }) });
      if (!response.ok) { setDetailError(await readError(response)); return; }
      await showDetail(detail.id);
      await load();
    } catch (cause) { setDetailError(cause instanceof Error ? cause.message : '更正失败'); }
  }

  async function previewDocument(documentId: number) {
    try {
      const response = await fetch(`/api/documents/${documentId}/file`, {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setDetailError(await readError(response));
        return;
      }

      const url = URL.createObjectURL(await response.blob());
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : '预览原件失败');
    }
  }

  async function previewSnapshot(sourceId: number) {
    try {
      const response = await fetch(
        `/api/document-sources/${sourceId}/snapshot`,
        { headers: authHeaders(token) },
      );

      if (!response.ok) {
        setDetailError(await readError(response));
        return;
      }

      const html = await response.text();
      const url = URL.createObjectURL(
        new Blob([html], { type: 'text/html;charset=utf-8' }),
      );
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : '查看来源快照失败');
    }
  }

  return (
    <section>
      <h2>产品主索引</h2>


      {error && <p className="error">{error}</p>}

      <div className="task-form">
        <label>产品名称<input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <label>公司<select value={companyId} onChange={(event) => setCompanyId(event.target.value)}><option value="">全部公司</option>{companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label>
        <label>接入状态<select value={integrationFilter} onChange={(event) => setIntegrationFilter(event.target.value)}><option value="">全部</option>{Object.entries(integrationStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>资料类型<select value={materialType} onChange={(event) => setMaterialType(event.target.value)}><option value="">全部</option><option value="clause">条款</option><option value="rate_table">费率表</option><option value="cash_value_table">现金价值表</option><option value="prospectus">说明书</option></select></label>
        <label>资料状态<select value={materialStatus} onChange={(event) => setMaterialStatus(event.target.value)}><option value="">全部</option>{Object.entries(materialStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>
          销售状态
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">全部</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary" onClick={() => { setPage(0); setDetail(null); setApplied({ statusFilter, query, companyId, integrationFilter, materialType, materialStatus }); }}>
          查询
        </button>
      </div>

      {busy && <p className="muted">处理中…</p>}

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>公司</th>
            <th>产品</th>
            <th>销售状态</th>
            <th>接入状态</th>
            <th>类别</th>
            <th>官网代码 / 备案号</th>
            <th>官网产品页</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={9} className="muted">
                还没有产品
              </td>
            </tr>
          ) : (
            items.map((item) => (
              <Fragment key={item.id}>
                <tr>
                  <td>{item.id}</td>
                <td>{item.companyName}</td>
                <td>
                  {item.confirmedName ?? item.name}
                  {item.confirmedName && (
                    <div className="muted">人工确认名称</div>
                  )}
                  {item.sameNameCount > 1 && (
                    <div className="muted">同名协会记录：{item.sameNameCount} 条</div>
                  )}
                </td>
                <td>
                  <span
                    className={`badge ${
                      item.confirmedSalesStatus ?? item.salesStatus
                    }`}
                  >
                    {statusLabels[
                      item.confirmedSalesStatus ?? item.salesStatus
                    ] ?? (item.confirmedSalesStatus ?? item.salesStatus)}
                  </span>
                </td>
                <td>
                  <span className="badge">
                    {integrationStatusLabels[item.integrationStatus] ??
                      item.integrationStatus}
                  </span>
                  {item.integrationStatusReason && (
                    <div className="muted">
                      {item.integrationStatusReason}
                    </div>
                  )}
                </td>
                <td>{item.categoryName}</td>
                <td>
                  {item.officialProductCode ?? '—'} /{' '}
                  {item.filingNumber ?? '—'}
                </td>
                <td>
                  {item.officialProductUrl ? (
                    <a
                      href={item.officialProductUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      官网产品页
                    </a>
                  ) : (
                    <span className="muted">待补</span>
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => detail?.id === item.id ? setDetail(null) : void showDetail(item.id)}
                  >
                    {detail?.id === item.id ? '收起' : '详情'}
                  </button>
                </td>
                </tr>
                {detail?.id === item.id && (
                  <tr>
                    <td colSpan={9}>
                      <div className="expanded-panel">
                      <ProductDetailPanel
                        detail={detail}
                        token={token}
                        onPreviewDocument={(documentId) =>
                          void previewDocument(documentId)
                        }
                        onPreviewSnapshot={(sourceId) =>
                          void previewSnapshot(sourceId)
                        }
                      />
                      <div className="task-form">
                        <label>确认产品名称<input value={editName} onChange={(event) => setEditName(event.target.value)} /></label>
                        <label>确认销售状态<select value={editStatus} onChange={(event) => setEditStatus(event.target.value)}>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                        <label>更正说明<input value={editNote} onChange={(event) => setEditNote(event.target.value)} /></label>
                        <button type="button" onClick={() => void saveCorrection()}>保存人工更正</button>
                      </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))
          )}
        </tbody>
      </table>
      <div className="actions" aria-label="产品分页"><button type="button" disabled={page === 0} onClick={() => { setDetail(null); setPage(page - 1); }}>上一页</button><span>第 {page + 1} 页</span><button type="button" disabled={!hasMore} onClick={() => { setDetail(null); setPage(page + 1); }}>下一页</button></div>

      {detailError && <p className="error">{detailError}</p>}
    </section>
  );
}
