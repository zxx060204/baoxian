import { useEffect, useState } from 'react';

import { authHeaders, readError } from './api';

interface CoverageRow {
  taskId: number;
  categoryCode: string;
  categoryName: string;
  status: string;
  pageFrom: number | null;
  pageTo: number | null;
  reportedTotal: number | null;
  readCount: number;
  uniqueCount: number;
  duplicateCount: number;
  failedUnits: number;
  failures: { page: number; error: string | null; kind: string }[];
}

interface P2CompanyRow {
  companyId: number;
  companyName: string;
  products: number;
  documents: number;
  links: number;
  failures: Record<string, number>;
  materials: Record<string, Record<string, number>>;
}

interface P2Report {
  companies: P2CompanyRow[];
  totals: {
    products: number;
    documents: number;
    links: number;
    failures: number;
  };
}

const materialLabels: Record<string, string> = {
  clause: '条款',
  rate_table: '费率表',
  prospectus: '产品说明书',
  cash_value_table: '现金价值表',
};

const materialStatusLabels: Record<string, string> = {
  downloaded: '已下载',
  not_found: '未找到',
  download_failed: '下载失败',
  attribution_pending: '归属待确认',
  not_run: '尚未执行',
};

const statusLabels: Record<string, string> = {
  queued: '排队',
  running: '运行中',
  completed: '完成',
  partial_failed: '部分失败',
  failed: '失败',
  cancelled: '已取消',
};

export default function ReportsPage({ token }: { token: string }) {
  const [items, setItems] = useState<CoverageRow[]>([]);
  const [p2, setP2] = useState<P2Report | null>(null);
  const [indexPage, setIndexPage] = useState(0);
  const [companyPage, setCompanyPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    if (!token) {
      setError('请先在页面顶部输入内部访问令牌');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const response = await fetch('/api/reports/coverage', {
        headers: authHeaders(token),
      });
      const p2Response = await fetch('/api/reports/p2', {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const data = (await response.json()) as { items: CoverageRow[] };
      setItems(data.items);
      setIndexPage((page) => Math.min(page, Math.max(0, Math.ceil(data.items.length / 10) - 1)));

      if (p2Response.ok) {
        const report = (await p2Response.json()) as P2Report;
        setP2(report);
        setCompanyPage((page) => Math.min(page, Math.max(0, Math.ceil(report.companies.length / 10) - 1)));
      } else {
        setP2(null);
        setError(`加载公司资料报告失败：${await readError(p2Response)}`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载报告失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  return (
    <section>
      <h2>索引录入报告</h2>

      {error && <p className="error">{error}</p>}
      {busy && <p className="muted">处理中…</p>}

      <div className="task-form">
        <button type="button" className="secondary" onClick={() => void load()}>
          刷新
        </button>
      </div>

      <table>
        <thead>
          <tr>
            <th>任务</th>
            <th>类别</th>
            <th>状态</th>
            <th>页范围</th>
            <th>源站总数</th>
            <th>读取</th>
            <th>去重后</th>
            <th>重复</th>
            <th>失败单元</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={9} className="muted">
                还没有任务报告
              </td>
            </tr>
          ) : (
            items.slice(indexPage * 10, indexPage * 10 + 10).map((item) => (
              <tr key={item.taskId}>
                <td>#{item.taskId}</td>
                <td>
                  {item.categoryName}
                </td>
                <td>{statusLabels[item.status] ?? item.status}</td>
                <td>
                  {item.pageFrom ?? '—'} – {item.pageTo ?? '—'}
                </td>
                <td>{item.reportedTotal ?? '—'}</td>
                <td>{item.readCount}</td>
                <td>{item.uniqueCount}</td>
                <td>{item.duplicateCount}</td>
                <td>
                  {item.failedUnits}
                  {item.failures.length > 0 && (
                    <div className="muted">
                      {item.failures
                        .map(
                          (failure) =>
                            `第 ${failure.page} 页 [${failure.kind}]：${failure.error ?? '未知错误'}`,
                        )
                        .join('；')}
                    </div>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="actions" aria-label="索引报告分页">
        <button type="button" disabled={indexPage === 0} onClick={() => setIndexPage(indexPage - 1)}>上一页</button>
        <span>第 {indexPage + 1} 页 / 共 {Math.max(1, Math.ceil(items.length / 10))} 页</span>
        <button type="button" disabled={(indexPage + 1) * 10 >= items.length} onClick={() => setIndexPage(indexPage + 1)}>下一页</button>
      </div>

      <h3>公司资料获取</h3>
      {p2 ? (
        <>
          <p className="muted">
            产品 {p2.totals.products} · 文件 {p2.totals.documents} · 关联{' '}
            {p2.totals.links} · 失败 {p2.totals.failures}
          </p>
          <table>
            <thead>
              <tr>
                <th>公司</th>
                <th>产品</th>
                <th>文件</th>
                <th>关联</th>
                <th>失败</th>
                <th>四类资料</th>
              </tr>
            </thead>
            <tbody>
              {p2.companies.slice(companyPage * 10, companyPage * 10 + 10).map((company) => (
                <tr key={company.companyId}>
                  <td>{company.companyName}</td>
                  <td>{company.products}</td>
                  <td>{company.documents}</td>
                  <td>{company.links}</td>
                  <td>
                    {Object.entries(company.failures)
                      .map(([reason, count]) => `${reason} ${count}`)
                      .join(' · ') || '0'}
                  </td>
                  <td>
                    {Object.entries(company.materials).map(
                      ([type, counts]) => (
                        <div key={type}>
                          {materialLabels[type] ?? type}：
                          {Object.entries(counts)
                            .map(
                              ([status, count]) =>
                                `${materialStatusLabels[status] ?? status} ${count}`,
                            )
                            .join(' · ')}
                        </div>
                      ),
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="actions" aria-label="公司资料报告分页">
            <button type="button" disabled={companyPage === 0} onClick={() => setCompanyPage(companyPage - 1)}>上一页</button>
            <span>第 {companyPage + 1} 页 / 共 {Math.max(1, Math.ceil(p2.companies.length / 10))} 页</span>
            <button type="button" disabled={(companyPage + 1) * 10 >= p2.companies.length} onClick={() => setCompanyPage(companyPage + 1)}>下一页</button>
          </div>
        </>
      ) : (
        <p className="muted">暂无 P2 报告</p>
      )}
    </section>
  );
}
