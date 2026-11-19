import { useEffect, useState, type FormEvent } from 'react';

import { authHeaders, readError } from './api';

interface Company {
  id: number;
  name: string;
  officialSiteUrl: string | null;
  documentEntryUrl: string | null;
  notes: string | null;
}

interface CompanyForm {
  name: string;
  officialSiteUrl: string;
  documentEntryUrl: string;
  notes: string;
}

const emptyForm: CompanyForm = {
  name: '',
  officialSiteUrl: '',
  documentEntryUrl: '',
  notes: '',
};

export default function CompaniesPage({ token }: { token: string }) {
  const [items, setItems] = useState<Company[]>([]);
  const [page, setPage] = useState(0);
  const [form, setForm] = useState<CompanyForm>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
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
      const response = await fetch('/api/companies', {
        headers: authHeaders(token),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const data = (await response.json()) as { items: Company[] };
      setItems(data.items);
      setPage(current => Math.min(current, Math.max(0, Math.ceil(data.items.length / 10) - 1)));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function resetForm() {
    setForm(emptyForm);
    setEditingId(null);
    setNotice(null);
  }

  function edit(company: Company) {
    setEditingId(company.id);
    setForm({
      name: company.name,
      officialSiteUrl: company.officialSiteUrl ?? '',
      documentEntryUrl: company.documentEntryUrl ?? '',
      notes: company.notes ?? '',
    });
    setError(null);
    setNotice(null);
  }

  async function createDocumentTask(id: number) {
    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const response = await fetch(`/api/companies/${id}/document-tasks`, {
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

      const task = (await response.json()) as { id: number };
      setNotice(`公司资料任务 #${task.id} 已排队`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发起资料任务失败');
    } finally {
      setBusy(false);
    }
  }

  async function discoverSite(companyId?: number) {
    setError(null);
    setNotice(null);
    setBusy(true);

    try {
      const response = await fetch('/api/crawl-tasks', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({
          type: 'company_site_discovery',
          ...(companyId === undefined ? {} : { companyIds: [companyId] }),
        }),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const task = (await response.json()) as { id: number; scope: { companies?: unknown[] } };
      setNotice(companyId === undefined
        ? `官网发现任务 #${task.id} 已排队，共 ${task.scope.companies?.length ?? 0} 家公司`
        : `公司官网发现任务 #${task.id} 已排队`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发起官网发现失败');
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);

    const payload = {
      name: form.name,
      officialSiteUrl: form.officialSiteUrl || null,
      documentEntryUrl: form.documentEntryUrl || null,
      notes: form.notes || null,
    };

    try {
      const response = await fetch(
        editingId ? `/api/companies/${editingId}` : '/api/companies',
        {
          method: editingId ? 'PATCH' : 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(token),
          },
          body: JSON.stringify(payload),
        },
      );

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      setNotice(editingId ? '公司已更新' : '公司已创建');
      setForm(emptyForm);
      setEditingId(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>公司目录</h2>


      <div className="actions">
        <button type="button" disabled={busy || !items.some(company => !company.officialSiteUrl)} onClick={() => void discoverSite()}>一键发现官网</button>
        <span className="muted">未设置官网：{items.filter(company => !company.officialSiteUrl).length} 家</span>
        <button type="button" className="secondary" disabled={busy} onClick={() => void load()}>刷新公司目录</button>
      </div>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}

      <form className="company-form" onSubmit={(event) => void submit(event)}>
        <label>
          公司名称
          <input
            value={form.name}
            onChange={(event) =>
              setForm({ ...form, name: event.target.value })
            }
            placeholder="例如：示例人寿保险股份有限公司"
            maxLength={255}
            required
          />
        </label>
        <label>
          公司官网首页
          <input
            type="url"
            value={form.officialSiteUrl}
            onChange={(event) =>
              setForm({ ...form, officialSiteUrl: event.target.value })
            }
            placeholder="https://www.example.com"
          />
        </label>
        <label>
          资料目录入口
          <input type="url" value={form.documentEntryUrl} onChange={(event) => setForm({ ...form, documentEntryUrl: event.target.value })} placeholder="https://www.example.com/disclosure" />
        </label>
        <label className="wide">
          备注
          <input
            value={form.notes}
            onChange={(event) =>
              setForm({ ...form, notes: event.target.value })
            }
            maxLength={2000}
            placeholder="可选"
          />
        </label>

        <div className="actions wide">
          <button type="submit" disabled={busy}>
            {editingId ? '保存修改' : '新增公司'}
          </button>
          {editingId !== null && (
            <button type="button" className="secondary" onClick={resetForm}>
              取消编辑
            </button>
          )}
        </div>
      </form>

      {busy && <p className="muted">处理中…</p>}

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>官网</th>
            <th>备注</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                还没有公司
              </td>
            </tr>
          ) : (
            items.slice(page * 10, page * 10 + 10).map((company) => (
              <tr key={company.id}>
                <td>{company.id}</td>
                <td>{company.name}</td>
                <td>
                  {company.officialSiteUrl ? (
                    <a
                      href={company.officialSiteUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开
                    </a>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{company.notes ?? '—'}</td>
                <td>
                  <div className="actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => edit(company)}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={!company.officialSiteUrl}
                      onClick={() => void createDocumentTask(company.id)}
                    >
                      发起产品资料获取
                    </button>
                    {!company.officialSiteUrl && (
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy}
                        onClick={() => void discoverSite(company.id)}
                      >
                        发现官网
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="actions" aria-label="公司分页"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(items.length / 10))} 页（{items.length} 条）</span><button type="button" disabled={(page + 1) * 10 >= items.length} onClick={() => setPage(page + 1)}>下一页</button></div>
    </section>
  );
}
