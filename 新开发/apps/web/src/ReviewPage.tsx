import { useEffect, useState } from 'react';

import { authHeaders, readError } from './api';

interface ReviewCandidate {
  id: number;
  name: string;
  salesStatus: string;
  salesStatusRaw: string | null;
}

interface ReviewItem {
  id: number;
  type: string;
  status: string;
  taskId?: number | null;
  payload: {
    kind?: 'product_identity' | 'document_attribution' | 'company_site';
    sourceId?: number;
    documentId?: number;
    companyId?: number;
    companyName?: string;
    reason?: string;
    siteCandidates?: {
      url: string;
      title?: string;
      snippet?: string;
      score: number;
      verified?: boolean;
    }[];
    incoming?: {
      companyName: string;
      name: string;
      salesStatus: string;
      salesStatusRaw: string | null;
      sourceUrl: string;
      category: { code: string; name: string };
    };
    candidates?: ReviewCandidate[];
    candidate?: {
      title: string;
      type: string;
      downloadUrl: string;
      sourcePageUrl: string;
      officialProductUrl?: string;
      productIds: number[];
    };
    productId?: number;
    field?: string;
    confirmedValue?: string;
    collectedValue?: string;
  };
}

const statusLabels: Record<string, string> = {
  on_sale: '在售',
  off_sale: '停售',
  disabled: '停用',
  unknown: '未知',
};
const materialLabels: Record<string, string> = { clause: '条款', rate_table: '费率表', cash_value_table: '现金价值表', prospectus: '产品说明书' };
const fieldLabels: Record<string, string> = { name: '产品名称', salesStatus: '销售状态' };

const typeLabels: Record<string, string> = {
  product_identity: '产品身份',
  document_attribution: '资料归属',
  sales_status_diff: '销售状态差异',
  correction_diff: '人工更正差异',
  source_value_diff: '来源值变化',
  company_site: '公司官网',
};

interface ProductLite {
  id: number;
  name: string;
  companyName: string;
}

export default function ReviewPage({ token }: { token: string }) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [products, setProducts] = useState<ProductLite[]>([]);
  const [selected, setSelected] = useState<Record<number, number>>({});
  const [selectedSite, setSelectedSite] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    if (!token) {
      setError('请先在页面顶部输入内部访问令牌');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const response = await fetch('/api/review-items?status=pending', {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const data = (await response.json()) as { items: ReviewItem[] };
      setItems(data.items);

      const productResponse = await fetch('/api/products', {
        headers: authHeaders(token),
      });

      if (productResponse.ok) {
        const productData = (await productResponse.json()) as {
          items: ProductLite[];
        };
        setProducts(productData.items);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载待确认项失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function choose(itemId: number, productId: number) {
    setSelected((current) => ({ ...current, [itemId]: productId }));
  }

  async function resolve(
    item: ReviewItem,
    action:
      | 'merge'
      | 'new'
      | 'assign'
      | 'ignore'
      | 'adopt'
      | 'keep'
      | 'select_site',
  ) {
    const productId =
      selected[item.id] ?? item.payload.candidates?.[0]?.id ?? undefined;
    const siteUrl = selectedSite[item.id];

    if ((action === 'merge' || action === 'assign') && !productId) {
      setError('请选择候选产品');
      return;
    }

    if (action === 'select_site' && !siteUrl) {
      setError('请选择官网候选');
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const body =
        action === 'merge' || action === 'assign'
          ? { action, productId }
          : action === 'select_site'
            ? { action, siteUrl }
            : { action };
      const response = await fetch(`/api/review-items/${item.id}/resolve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      setNotice(
        action === 'ignore'
          ? '已忽略'
          : action === 'adopt'
            ? '已采用采集值'
            : action === 'keep'
              ? '已保留人工确认值'
              : action === 'assign'
                ? `已指定到产品 #${productId}`
                : action === 'merge'
                  ? `已合并到产品 #${productId}`
                  : action === 'select_site'
                    ? `已设为官网：${siteUrl}`
                    : '已作为新产品写入',
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '处理失败');
    } finally {
      setBusy(false);
    }
  }

  const productLabel = (productId: number | undefined): string => {
    if (!productId) {
      return '—';
    }
    const product = products.find((item) => item.id === productId);
    return product
      ? `#${productId} ${product.companyName} · ${product.name}`
      : `#${productId}`;
  };

  return (
    <section>
      <h2>待确认（{items.length}）</h2>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      {busy && <p className="muted">处理中…</p>}

      {items.length === 0 ? (
        <p className="muted">没有待确认项</p>
      ) : (
        items.map((item) => {
          if (item.type === 'company_site') {
            const candidates = item.payload.siteCandidates ?? [];

            return (
              <article className="review-item" key={item.id}>
                <div className="review-header">
                  <strong>{item.payload.companyName ?? '公司官网待确认'}</strong>
                  <span className="badge pending">
                    {typeLabels[item.type] ?? item.type}
                  </span>
                </div>
                <p className="muted">
                  {item.payload.reason ?? '多个候选通过官网验证，无法自动选择。'}
                </p>
                <div className="candidate-list">
                  {candidates.map((candidate) => (
                    <div className={`site-candidate${selectedSite[item.id] === candidate.url ? ' selected' : ''}`} key={candidate.url}>
                      <span className="candidate-content"><a href={candidate.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>{candidate.url}</a>
                      <span className="candidate-title">{candidate.title ?? '—'}</span>
                      {candidate.snippet && <span className="candidate-description muted">{candidate.snippet}</span>}
                      <strong className="candidate-verification">{candidate.verified === false ? '未通过自动验证，请核实官网身份' : '已通过自动验证，请核实'}</strong></span>
                      <button type="button" className="secondary" aria-label={`选择候选 ${candidate.url}`} aria-pressed={selectedSite[item.id] === candidate.url} onClick={() => setSelectedSite((current) => ({ ...current, [item.id]: candidate.url }))}>{selectedSite[item.id] === candidate.url ? '已选择' : '选择此候选'}</button>
                    </div>
                  ))}
                </div>
                {candidates.length === 0 && <p className="muted">没有可展示的候选，请通过公司页面查找并填写官网，或忽略此项。</p>}
                <div className="actions">
                  <button
                    type="button"
                    disabled={busy || !selectedSite[item.id]}
                    onClick={() => void resolve(item, 'select_site')}
                  >
                    设为官网
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void resolve(item, 'ignore')}
                  >
                    忽略
                  </button>
                </div>
              </article>
            );
          }
          if (
            item.type === 'correction_diff' ||
            item.type === 'sales_status_diff' ||
            item.type === 'source_value_diff'
          ) {
            const isSourceChange = item.type === 'source_value_diff';

            return (
              <article className="review-item" key={item.id}>
                <div className="review-header">
                  <strong>
                    {productLabel(item.payload.productId)} ·{' '}
                    {isSourceChange ? '来源值变化' : '人工更正差异'}
                  </strong>
                  <span className="badge pending">
                    {typeLabels[item.type] ?? item.type}
                  </span>
                </div>
                <p className="muted">
                  字段：{fieldLabels[item.payload.field ?? ''] ?? '未知'} ·{' '}
                  {isSourceChange ? '上次来源值' : '确认值'}：
                  {item.payload.confirmedValue ?? '—'} · 本次采集值：
                  {item.payload.collectedValue ?? '—'}
                </p>
                <p className="muted">
                  采集结果未覆盖人工确认值；请选择采用采集值、保留确认值或忽略。
                </p>
                <div className="actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void resolve(item, 'adopt')}
                  >
                    采用采集值
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void resolve(item, 'keep')}
                  >
                    保留确认值
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void resolve(item, 'ignore')}
                  >
                    忽略
                  </button>
                </div>
              </article>
            );
          }

          if (item.type === 'document_attribution') {
            const candidate = item.payload.candidate;
            const candidateProducts = (candidate?.productIds ?? []).map(
              (productId) => {
                const product = products.find(
                  (itemProduct) => itemProduct.id === productId,
                );
                return {
                  id: productId,
                  label: productLabel(productId),
                  name: product?.name ?? null,
                  companyName: product?.companyName ?? null,
                };
              },
            );
            const sameName =
              candidateProducts.length > 1 &&
              new Set(
                candidateProducts.map((product) => product.name ?? ''),
              ).size < candidateProducts.length;
            const conflictText =
              candidateProducts.length > 1
                ? `矛盾：同一份文件同时匹配到 ${candidateProducts.length} 个产品，系统无法自动判断归属${
                    sameName ? '；这些产品名称相同，不能靠名称区分' : ''
                  }。`
                : candidateProducts.length === 0
                  ? '原因：系统未能将这份资料匹配到明确的产品，需要人工核实并指定。'
                  : '';

            return (
              <article className="review-item" key={item.id}>
                <div className="review-header">
                  <strong>{candidate?.title ?? '资料归属待确认'}</strong>
                  <span className="badge pending">
                    {typeLabels[item.type] ?? item.type}
                  </span>
                </div>
                <p className="muted">
                  任务 #{item.taskId ?? '—'} · 文件 #
                  {item.payload.documentId ?? '—'} · 资料类型：
                  {materialLabels[candidate?.type ?? ''] ?? '未知'}
                </p>
                {conflictText && <p className="error">{conflictText}</p>}
                <p className="muted">
                  候选产品：
                  {candidateProducts.length > 0
                    ? candidateProducts
                        .map((product) => product.label)
                        .join('；')
                    : '无'}
                </p>
                <p className="muted">
                  判断依据：文件标题「{candidate?.title ?? '—'}」；官网来源页；
                  候选产品名称与归属。确认后会把文件关联到所选产品。
                </p>
                <p className="muted">
                  官网来源页：
                  {candidate?.sourcePageUrl ? (
                    <a
                      href={candidate.sourcePageUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开
                    </a>
                  ) : (
                    '—'
                  )}
                  {candidate?.officialProductUrl ? (
                    <>
                      {' '}
                      · 官网产品页：
                      <a
                        href={candidate.officialProductUrl}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开
                      </a>
                    </>
                  ) : null}
                </p>
                {candidate?.downloadUrl ? (
                  <p className="muted">
                    文件地址：
                    <a
                      href={candidate.downloadUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开
                    </a>
                  </p>
                ) : null}
                <p className="muted">
                  确认后会把该文件关联到指定产品，并保留官网来源页和下载地址；忽略则不入库。
                </p>
                <div className="actions">
                  {candidateProducts.length > 0 ? (
                    <select
                      value={selected[item.id] ?? ''}
                      onChange={(event) =>
                        choose(item.id, Number(event.target.value))
                      }
                    >
                      <option value="">选择产品</option>
                      {candidateProducts.map((product) => (
                        <option key={product.id} value={product.id}>
                          {product.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="number"
                      min={1}
                      placeholder="产品 ID"
                      value={selected[item.id] ?? ''}
                      onChange={(event) =>
                        choose(item.id, Number(event.target.value))
                      }
                    />
                  )}
                  <button
                    type="button"
                    disabled={busy || !selected[item.id]}
                    onClick={() => void resolve(item, 'assign')}
                  >
                    指定到产品
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void resolve(item, 'ignore')}
                  >
                    忽略
                  </button>
                </div>
              </article>
            );
          }

          const incoming = item.payload.incoming;
          const candidateList = item.payload.candidates ?? [];

          if (!incoming) {
            return null;
          }

          const chosen = selected[item.id] ?? candidateList[0]?.id;

          return (
            <article className="review-item" key={item.id}>
              <div className="review-header">
                <strong>
                  {incoming.companyName} · {incoming.name}
                </strong>
                <span className="badge pending">
                  {typeLabels[item.type] ?? item.type}
                </span>
              </div>

              <p className="muted">
                采集状态：
                {statusLabels[incoming.salesStatus] ?? incoming.salesStatus}
                （原始值：{incoming.salesStatusRaw ?? '空'}）· 类别{' '}
                {incoming.category.name} ·{' '}
                <a
                  href={incoming.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  来源
                </a>
              </p>

              <p className="muted">
                原因：新采集的产品与现有产品可能是同一产品，系统无法可靠区分。
              </p>
              <p className="muted">
                候选产品（{candidateList.length}）
              </p>
              <div className="candidate-list">
                {candidateList.map((candidate) => (
                  <label className="candidate" key={candidate.id}>
                    <input
                      type="radio"
                      name={`review-${item.id}`}
                      checked={chosen === candidate.id}
                      onChange={() => choose(item.id, candidate.id)}
                    />
                    #{candidate.id} {candidate.name} ·{' '}
                    {statusLabels[candidate.salesStatus] ??
                      candidate.salesStatus}
                    （原始值：{candidate.salesStatusRaw ?? '空'}）
                  </label>
                ))}
              </div>

              <div className="actions">
                <button
                  type="button"
                  disabled={busy || !chosen}
                  onClick={() => void resolve(item, 'merge')}
                >
                  合并到选中候选
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => void resolve(item, 'new')}
                >
                  作为新产品
                </button>
              </div>
            </article>
          );
        })
      )}
    </section>
  );
}
