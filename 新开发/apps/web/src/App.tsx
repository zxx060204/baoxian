import { useEffect, useState } from 'react';

import CompaniesPage from './CompaniesPage';
import ProductsPage from './ProductsPage';
import ReportsPage from './ReportsPage';
import ReviewPage from './ReviewPage';
import SchedulesPage from './SchedulesPage';
import TasksPage from './TasksPage';

interface Health {
  status: string;
  service: string;
  version: string;
  timestamp: string;
}

interface SystemInfo {
  service: string;
  version: string;
  node?: string;
  python?: string;
  uptimeSeconds: number;
}

type View =
  | 'overview'
  | 'companies'
  | 'tasks'
  | 'products'
  | 'review'
  | 'reports'
  | 'settings';

function readStoredToken(): string {
  try {
    return sessionStorage.getItem('baoxian.token') ?? '';
  } catch {
    return '';
  }
}

export default function App() {
  const [view, setView] = useState<View>('overview');
  const [token, setToken] = useState(readStoredToken);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem('baoxian.token', token);
    } catch {
      // 会话存储不可用时仅保留当前页面状态
    }
  }, [token]);

  useEffect(() => {
    let active = true;

    void fetch('/health')
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return (await response.json()) as Health;
      })
      .then((data) => {
        if (active) {
          setHealth(data);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setHealthError(error instanceof Error ? error.message : '未知错误');
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function checkAuth() {
    setSystem(null);
    setAuthError(null);

    try {
      const response = await fetch('/api/system', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });

      if (!response.ok) {
        setAuthError(
          response.status === 401
            ? '未授权：令牌缺失或无效'
            : `请求失败：HTTP ${response.status}`,
        );
        return;
      }

      setSystem((await response.json()) as SystemInfo);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : '请求失败');
    }
  }

  return (
    <main>
      <header className="app-header">
        <div>
          <h1>保险产品数据中台</h1>
          <p className="subtitle">P1/P2 信息接入与资料获取 · 首轮内部环境</p>
        </div>
        <label className="token-field">
          <span className="muted">内部访问令牌</span>
          <input
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="dev-internal-token"
            aria-label="内部访问令牌"
          />
        </label>
      </header>

      <nav className="tabs">
        <button
          type="button"
          className={view === 'overview' ? 'active' : ''}
          onClick={() => setView('overview')}
        >
          概览
        </button>
        <button
          type="button"
          className={view === 'companies' ? 'active' : ''}
          onClick={() => setView('companies')}
        >
          公司
        </button>
        <button
          type="button"
          className={view === 'tasks' ? 'active' : ''}
          onClick={() => setView('tasks')}
        >
          任务
        </button>
        <button
          type="button"
          className={view === 'products' ? 'active' : ''}
          onClick={() => setView('products')}
        >
          产品
        </button>
        <button
          type="button"
          className={view === 'review' ? 'active' : ''}
          onClick={() => setView('review')}
        >
          待确认
        </button>
        <button
          type="button"
          className={view === 'reports' ? 'active' : ''}
          onClick={() => setView('reports')}
        >
          报告
        </button>
        <button
          type="button"
          className={view === 'settings' ? 'active' : ''}
          onClick={() => setView('settings')}
        >
          设置
        </button>
      </nav>

      {view === 'overview' ? (
        <>
          <section>
            <h2>API 状态</h2>
            {health ? (
              <p className="ok">
                服务 {health.service} · 版本 {health.version} · {health.status}
              </p>
            ) : healthError ? (
              <p className="error">无法连接 API：{healthError}</p>
            ) : (
              <p>检查中…</p>
            )}
          </section>

          <section>
            <h2>内部认证</h2>
            <button type="button" onClick={() => void checkAuth()}>
              检查认证
            </button>
            {system && (
              <p className="ok">
                已认证：{system.service} · {system.python ? `Python ${system.python}` : `Node ${system.node ?? '未知'}`} · 运行{' '}
                {system.uptimeSeconds}s
              </p>
            )}
            {authError && <p className="error">{authError}</p>}
          </section>
        </>
      ) : view === 'companies' ? (
        <CompaniesPage token={token} />
      ) : view === 'tasks' ? (
        <TasksPage token={token} />
      ) : view === 'products' ? (
        <ProductsPage token={token} />
      ) : view === 'review' ? (
        <ReviewPage token={token} />
      ) : view === 'reports' ? (
        <ReportsPage token={token} />
      ) : (
        <SchedulesPage token={token} />
      )}
    </main>
  );
}
