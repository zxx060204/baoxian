export function authHeaders(token: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      message?: string;
      details?: { message?: string }[];
    };
    return (
      body.details?.[0]?.message ?? body.message ?? `HTTP ${response.status}`
    );
  } catch {
    return `HTTP ${response.status}`;
  }
}

/**
 * 读取任务 SSE 流。使用 fetch 而不是 EventSource，以便携带内部令牌。
 * 任务进入终态后服务端结束流；断线重连由调用方重新查询任务状态。
 */
export async function subscribeTaskEvents(
  taskId: number,
  token: string,
  onTask: (task: Record<string, unknown>) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/crawl-tasks/${taskId}/events`, {
    headers: authHeaders(token),
    signal,
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  const reader = response.body?.getReader();

  if (!reader) {
    return;
  }

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();

    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf('\n\n');

    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      const dataLine = block
        .split('\n')
        .find((line) => line.startsWith('data: '));

      if (!dataLine) {
        continue;
      }

      try {
        const data = JSON.parse(dataLine.slice('data: '.length)) as Record<
          string,
          unknown
        >;

        if (typeof data.id === 'number') {
          onTask(data);
        }
      } catch {
        // 忽略无法解析的事件，下一次轮询仍可恢复状态
      }
    }
  }
}
