import { useEffect, useState, type FormEvent } from 'react';

import { authHeaders, readError } from './api';

interface RuntimeSettings {
  globalConcurrency: number;
  domainIntervalMs: number;
  requestTimeoutMs: number;
  retryAttempts: number;
  indexRefreshMinutes: number;
  associationCrawlEnabled: boolean;
}

interface Schedule {
  id: number;
  categoryCode: string;
  categoryName: string;
  everyMinutes: number;
  enabled: boolean;
}

interface Category {
  code: string;
  name: string;
}

interface SettingsForm {
  globalConcurrency: string;
  domainIntervalMs: string;
  requestTimeoutMs: string;
  retryAttempts: string;
  indexRefreshMinutes: string;
}

export default function SchedulesPage({ token }: { token: string }) {
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [form, setForm] = useState<SettingsForm>({
    globalConcurrency: '2',
    domainIntervalMs: '1000',
    requestTimeoutMs: '30000',
    retryAttempts: '3',
    indexRefreshMinutes: '10080',
  });
  const [crawlEnabled, setCrawlEnabled] = useState(true);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [category, setCategory] = useState('');
  const [everyMinutes, setEveryMinutes] = useState('60');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadAll() {
    if (!token) {
      setError('请先在页面顶部输入内部访问令牌');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const [settingsResponse, schedulesResponse, categoriesResponse] =
        await Promise.all([
          fetch('/api/settings/runtime', { headers: authHeaders(token) }),
          fetch('/api/schedules', { headers: authHeaders(token) }),
          fetch('/api/categories', { headers: authHeaders(token) }),
        ]);

      for (const response of [
        settingsResponse,
        schedulesResponse,
        categoriesResponse,
      ]) {
        if (!response.ok) {
          setError(await readError(response));
          return;
        }
      }

      const settingsData = (await settingsResponse.json()) as RuntimeSettings;
      const schedulesData = (await schedulesResponse.json()) as {
        items: Schedule[];
      };
      const categoriesData = (await categoriesResponse.json()) as {
        items: Category[];
      };

      setSettings(settingsData);
      setForm({
        globalConcurrency: String(settingsData.globalConcurrency),
        domainIntervalMs: String(settingsData.domainIntervalMs),
        requestTimeoutMs: String(settingsData.requestTimeoutMs),
        retryAttempts: String(settingsData.retryAttempts),
        indexRefreshMinutes: String(settingsData.indexRefreshMinutes),
      });
      setCrawlEnabled(settingsData.associationCrawlEnabled);
      setSchedules(schedulesData.items);
      setCategories(categoriesData.items);
      setCategory((current) => current || categoriesData.items[0]?.code || '');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '加载设置失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const response = await fetch('/api/settings/runtime', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({
          globalConcurrency: Number(form.globalConcurrency),
          domainIntervalMs: Number(form.domainIntervalMs),
          requestTimeoutMs: Number(form.requestTimeoutMs),
          retryAttempts: Number(form.retryAttempts),
          indexRefreshMinutes: Number(form.indexRefreshMinutes),
          associationCrawlEnabled: crawlEnabled,
        }),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      const updated = (await response.json()) as RuntimeSettings;
      setSettings(updated);
      setNotice('运行设置已保存；对之后创建的任务生效');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存设置失败');
    } finally {
      setBusy(false);
    }
  }

  async function createSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const response = await fetch('/api/schedules', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({
          category,
          everyMinutes: Number(everyMinutes),
        }),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      setNotice('定时计划已创建');
      await loadAll();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建计划失败');
    } finally {
      setBusy(false);
    }
  }

  async function toggleSchedule(schedule: Schedule) {
    setBusy(true);
    setError(null);
    setNotice(null);

    try {
      const response = await fetch(`/api/schedules/${schedule.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({ enabled: !schedule.enabled }),
      });

      if (!response.ok) {
        setError(await readError(response));
        return;
      }

      setNotice(schedule.enabled ? '计划已停用' : '计划已启用');
      await loadAll();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '更新计划失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>运行设置与定时计划</h2>

      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}

      <h3>运行设置</h3>
      <form className="company-form" onSubmit={(event) => void saveSettings(event)}>
        <label>
          索引完整复查周期（分钟）
          <input type="number" min={1} max={525600} value={form.indexRefreshMinutes} onChange={(event) => setForm({ ...form, indexRefreshMinutes: event.target.value })} />
        </label>
        <label>
          全局并发
          <input
            type="number"
            min={1}
            max={10}
            value={form.globalConcurrency}
            onChange={(event) =>
              setForm({ ...form, globalConcurrency: event.target.value })
            }
          />
        </label>
        <label>
          单域名访问间隔（毫秒）
          <input
            type="number"
            min={100}
            max={60000}
            value={form.domainIntervalMs}
            onChange={(event) =>
              setForm({ ...form, domainIntervalMs: event.target.value })
            }
          />
        </label>
        <label>
          请求超时（毫秒）
          <input
            type="number"
            min={1000}
            max={120000}
            value={form.requestTimeoutMs}
            onChange={(event) =>
              setForm({ ...form, requestTimeoutMs: event.target.value })
            }
          />
        </label>
        <label>
          重试次数
          <input
            type="number"
            min={0}
            max={10}
            value={form.retryAttempts}
            onChange={(event) =>
              setForm({ ...form, retryAttempts: event.target.value })
            }
          />
        </label>
        <label className="wide">
          <span>
            <input
              type="checkbox"
              checked={crawlEnabled}
              onChange={(event) => setCrawlEnabled(event.target.checked)}
            />{' '}
            允许创建协会索引任务
          </span>
        </label>
        <div className="actions wide">
          <button type="submit" disabled={busy}>
            保存设置
          </button>
        </div>
      </form>

      <h3>定时增量计划</h3>
      <form className="task-form" onSubmit={(event) => void createSchedule(event)}>
        <label>
          协会类别
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            {categories.map((item) => (
              <option key={item.code} value={item.code}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          频率（分钟）
          <input
            type="number"
            min={1}
            max={1440}
            value={everyMinutes}
            onChange={(event) => setEveryMinutes(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !category}>
          创建计划
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => void loadAll()}
        >
          刷新
        </button>
      </form>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>类别</th>
            <th>频率</th>
            <th>启用</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {schedules.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                还没有定时计划
              </td>
            </tr>
          ) : (
            schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td>{schedule.id}</td>
                <td>
                  {schedule.categoryName}
                </td>
                <td>每 {schedule.everyMinutes} 分钟</td>
                <td>{schedule.enabled ? '启用' : '停用'}</td>
                <td>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void toggleSchedule(schedule)}
                  >
                    {schedule.enabled ? '停用' : '启用'}
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
}
