import { useEffect, useId, useRef, useState } from 'react';
import { authHeaders, readError } from './api';

const schemas: Record<string, string> = {
  clause: 'document-content-v1',
  rate_table: 'mapping-input-v1',
  cash_value_table: 'mapping-input-v1',
  prospectus: 'document-content-v1',
};

const unavailableLabels: Record<string, string> = {
  not_found: '未找到资料',
  download_failed: '下载失败',
  attribution_pending: '归属待确认',
};

const pageIssues: Record<string, string> = {
  needs_ocr: '此页需要 OCR，当前未能生成正文。',
  failed: '此页解析失败。',
  not_processed_limit: '此页达到解析上限，尚未处理。',
};

type GridCell = [text: string, rowspan: number, colspan: number] | -1 | null;

interface LayoutTable {
  table_id: string;
  page: number;
  row_count: number;
  column_count: number;
  header_rows: number[];
  structure_status: 'complete' | 'partial' | 'fragment';
  continuation_of: string | null;
  continuation_status?: string;
  grid: GridCell[][];
  tokens?: { text: string; row_index: number | null }[];
}

interface NumericLayout {
  schema: 'mapping-input-v1';
  pages: { page: number }[];
  contexts: {
    context_id: string;
    text: string;
    page: number;
    relations?: { table_id: string; position: 'before' | 'after' | 'between_tables' }[];
  }[];
  tables: LayoutTable[];
}

interface TextLayout {
  schema: 'document-content-v1';
  status: 'completed' | 'completed_with_warnings' | 'partial' | 'failed';
  pages: {
    page_number: number;
    status: string;
    review_reasons?: string[];
  }[];
  chunks: {
    chunk_id: string;
    kind: 'text' | 'table_reference' | 'image_reference';
    raw_text: string;
    page_number: number;
    table_id?: string | null;
  }[];
  tables: LayoutTable[];
}

type Layout = NumericLayout | TextLayout;

const storedStatusLabels: Record<string, string> = {
  completed: '解析结果已保存',
  completed_with_warnings: '解析结果已保存，有待复核项',
  partial: '部分页面未解析，结果已保存',
  failed: '未提取到页面，可查看解析记录',
};

function errorMessage(cause: unknown) {
  return cause instanceof Error ? cause.message : '请求失败';
}

function layoutError(message: string, reason?: string) {
  return reason === 'compact_native_page_needs_ocr'
    ? 'PDF 含扫描页或未识别文字，数值表解析需要 OCR。'
    : [message, reason].filter(Boolean).join(' · ');
}

function resultStatus(layout: Layout) {
  if (layout.schema === 'document-content-v1') {
    return {
      completed: '解析完成',
      completed_with_warnings: '解析完成，有待复核项',
      partial: '部分页面未解析',
      failed: '未提取到页面',
    }[layout.status];
  }
  if (!layout.tables.length) return '解析完成，未识别到表格';
  return layout.tables.some((table) =>
    table.structure_status !== 'complete' || table.continuation_status === 'ambiguous')
    ? '解析完成，有待复核项'
    : '解析完成';
}

function TableLayout({ table, number, continuationPage }: {
  table: LayoutTable;
  number: number;
  continuationPage?: number;
}) {
  const captionId = useId();
  const caption = `第 ${table.page} 页 · 表 ${number}`;
  const headers = new Set(table.header_rows);
  let headerCount = 0;
  while (headerCount < table.grid.length && headers.has(headerCount)) headerCount++;
  // HTML row spans cannot cross table row groups.
  if (table.grid.slice(0, headerCount).some((row, index) =>
    row.some((cell) => cell && cell !== -1 && index + cell[1] > headerCount))) {
    headerCount = 0;
  }

  const rows = table.grid.map((row, rowIndex) => (
    <tr key={rowIndex}>
      {row.map((cell, column) => {
        if (cell === -1) return null;
        if (cell === null) return (
          <td key={column} className="parse-grid-gap">
            <span aria-hidden="true">?</span><span className="parse-visually-hidden">网格缺口</span>
          </td>
        );
        const [value, rowSpan, colSpan] = cell;
        return headers.has(rowIndex) ? (
          <th key={column} scope={colSpan === 1 ? 'col' : undefined} rowSpan={rowSpan} colSpan={colSpan}>
            {value}
          </th>
        ) : <td key={column} rowSpan={rowSpan} colSpan={colSpan}>{value}</td>;
      })}
    </tr>
  ));

  return (
    <div className="parse-table">
      {table.structure_status === 'fragment' ? (
        <>
          <h6>{caption} · 待复核片段</h6>
          <p className="muted">此片段尚未形成可用网格。</p>
        </>
      ) : (
        <div className="parse-grid-scroll" tabIndex={0} role="region" aria-labelledby={captionId}>
          <table>
            <caption id={captionId}>{caption} · {table.row_count} 行 × {table.column_count} 列</caption>
            {headerCount > 0 && <thead>{rows.slice(0, headerCount)}</thead>}
            <tbody>{rows.slice(headerCount)}</tbody>
          </table>
        </div>
      )}
      {table.structure_status === 'partial' && <p className="parse-note">部分网格待复核。问号表示网格缺口。</p>}
      {continuationPage !== undefined && <p className="muted">续接第 {continuationPage} 页的表格</p>}
      {table.continuation_status === 'ambiguous' && <p className="parse-note">续表归属待复核</p>}
      {table.tokens?.length ? (
        <details open={table.structure_status === 'fragment' || undefined}>
          <summary>识别到的文字片段</summary>
          <p className="parse-text">{table.tokens.map((token, index, tokens) => (
            (index === 0 ? '' : tokens[index - 1]?.row_index === token.row_index ? ' ' : '\n') + token.text
          )).join('')}</p>
        </details>
      ) : null}
    </div>
  );
}

function LayoutPreview({ layout, label, onPreview }: {
  layout: Layout;
  label: string;
  onPreview: () => void;
}) {
  const numeric = layout.schema === 'mapping-input-v1';
  const pageNumbers = numeric ? layout.pages.map((page) => page.page) : layout.pages.map((page) => page.page_number);
  const [pageNumber, setPageNumber] = useState((numeric ? layout.tables[0]?.page : undefined) ?? pageNumbers[0]);
  const page = numeric ? undefined : layout.pages.find((item) => item.page_number === pageNumber);
  const tables = layout.tables.filter((table) => table.page === pageNumber);
  const contexts = numeric ? layout.contexts.filter((context) => context.page === pageNumber) : [];
  const placedContexts = contexts.map((context) => {
    const relations = context.relations ?? [];
    const relationIndex = relations.findIndex((relation) => tables.some((table) => table.table_id === relation.table_id));
    const relation = relations[relationIndex];
    const placement = relation?.position === 'between_tables'
      ? (relationIndex === 0 ? 'after' : 'before')
      : relation?.position;
    return { context, tableId: relation?.table_id, placement };
  });
  const chunks = numeric ? [] : layout.chunks.filter((chunk) => chunk.page_number === pageNumber);
  const tableReferences = new Set(chunks.filter((chunk) => chunk.kind === 'table_reference').map((chunk) => chunk.table_id));

  function renderContexts(tableId?: string, placement?: 'before' | 'after') {
    return placedContexts.filter((item) => item.tableId === tableId && item.placement === placement)
      .map(({ context }) => <p className="parse-text" key={context.context_id}>{context.text}</p>);
  }

  function renderTable(table: LayoutTable) {
    return <TableLayout
      key={table.table_id}
      table={table}
      number={tables.indexOf(table) + 1}
      continuationPage={layout.tables.find((candidate) => candidate.table_id === table.continuation_of)?.page}
    />;
  }

  return (
    <div className="parse-result">
      <div className="parse-toolbar">
        <h5>{label} · 解析结果</h5>
        <button type="button" className="secondary" onClick={onPreview}>对照原件</button>
      </div>
      {pageNumbers.length > 0 && (
        <div className="parse-toolbar parse-pagination">
          <label>原件页码 <select value={pageNumber} onChange={(event) => setPageNumber(Number(event.target.value))}>
            {pageNumbers.map((number) => <option key={number} value={number}>第 {number} 页</option>)}
          </select></label>
          <span className="muted">共 {pageNumbers.length} 页 · {layout.tables.length} 张表</span>
        </div>
      )}
      {page && pageIssues[page.status] && <p className="parse-note">{pageIssues[page.status]}</p>}
      {page?.review_reasons?.length ? (
        <details>
          <summary>本页复核原因</summary>
          <ul>{page.review_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </details>
      ) : null}
      {renderContexts()}
      {chunks.map((chunk) => {
        if (chunk.kind === 'text') return <p className="parse-text" key={chunk.chunk_id}>{chunk.raw_text}</p>;
        if (chunk.kind === 'image_reference') return <p className="muted" key={chunk.chunk_id}>图片内容见 PDF 原件</p>;
        const table = tables.find((item) => item.table_id === chunk.table_id);
        return table ? renderTable(table) : <p className="parse-note" key={chunk.chunk_id}>此处有表格引用，未生成可展示的表格。</p>;
      })}
      {tables.filter((table) => !tableReferences.has(table.table_id)).map((table) => (
        <div key={table.table_id}>
          {renderContexts(table.table_id, 'before')}
          {renderTable(table)}
          {renderContexts(table.table_id, 'after')}
        </div>
      ))}
      {!tables.length && !contexts.length && !chunks.length && !(page && pageIssues[page.status]) && (
        <p className="muted">{pageNumbers.length ? '本页未提取到可展示的内容' : '未提取到可展示的页面'}</p>
      )}
    </div>
  );
}

export default function DocumentParsingPanel({ productId, token, materials, sources, onPreview }: {
  productId: number;
  token: string;
  materials: { type: string; label: string; status: string }[];
  sources: { documentId: number; type: string; mimeType?: string }[];
  onPreview: (documentId: number) => void;
}) {
  const [saved, setSaved] = useState<Record<number, { layout?: Layout; status?: string }>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [selectedId, setSelectedId] = useState<number>();
  const [announcement, setAnnouncement] = useState('');
  const [batchMessage, setBatchMessage] = useState('');
  const [storageMessage, setStorageMessage] = useState('');
  const [busy, setBusy] = useState<{ kind: 'batch' | 'parse' | 'view'; id?: number }>();
  const request = useRef<AbortController | null>(null);
  const titleId = useId();
  const productPath = `/api/products/${productId}/parsed-layouts`;

  useEffect(() => {
    const controller = new AbortController();
    async function loadSaved() {
      try {
        const response = await fetch(productPath, { headers: authHeaders(token), signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(await readError(response));
        const { items } = await response.json() as { items: { documentId: number }[] };
        if (!controller.signal.aborted) {
          setSaved((previous) => ({ ...Object.fromEntries(items.map(({ documentId }) => [documentId, {}])), ...previous }));
        }
      } catch (cause) {
        if (!controller.signal.aborted) setStorageMessage(`无法读取已保存的解析状态：${errorMessage(cause)}`);
      }
    }
    void loadSaved();
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [productPath, token]);

  function begin(kind: 'batch' | 'parse' | 'view', id?: number) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy({ kind, id });
    return controller;
  }

  function finish(controller: AbortController) {
    if (request.current !== controller) return;
    request.current = null;
    if (!controller.signal.aborted) setBusy(undefined);
  }

  async function openLayout(documentId: number, type: string, label: string, parse: boolean) {
    if (request.current) return;
    if (!parse && saved[documentId]?.layout) {
      setSelectedId(documentId);
      return;
    }
    const controller = begin(parse ? 'parse' : 'view', documentId);
    if (!controller) return;
    setErrors((previous) => ({ ...previous, [documentId]: '' }));
    setAnnouncement(`${label}${parse ? '正在解析' : '正在读取结果'}`);

    try {
      const suffix = parse && saved[documentId] ? '?force=true' : '';
      const response = await fetch(`/api/documents/${documentId}/saved-layout${suffix}`, {
        method: parse ? 'POST' : 'GET',
        headers: authHeaders(token),
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!response.ok) {
        const reason = await response.clone().json().then((body) => body.reason).catch(() => undefined);
        throw new Error(layoutError(await readError(response), reason));
      }
      const layout = await response.json() as Layout;
      if (layout.schema !== schemas[type]) throw new Error('解析接口返回了无法识别的 layout');
      if (controller.signal.aborted) return;
      setSaved((previous) => ({ ...previous, [documentId]: { layout } }));
      setSelectedId(documentId);
      setAnnouncement(`${label} · ${resultStatus(layout)}`);
    } catch (cause) {
      if (controller.signal.aborted) return;
      const message = errorMessage(cause);
      setErrors((previous) => ({ ...previous, [documentId]: message }));
      setAnnouncement(`${label} · ${message}`);
    } finally {
      finish(controller);
    }
  }

  async function parseAll() {
    const controller = begin('batch');
    if (!controller) return;
    setBatchMessage('正在解析本产品的已获取资料…');

    try {
      const response = await fetch(productPath, { method: 'POST', headers: authHeaders(token), signal: controller.signal });
      if (!response.ok) throw new Error(await readError(response));
      const { items } = await response.json() as { items: {
        documentId: number; state: 'saved' | 'reused' | 'failed'; layoutStatus?: string; reason?: string;
      }[] };
      if (controller.signal.aborted) return;
      const persisted = items.filter((item) => item.state !== 'failed');
      setSaved((previous) => ({
        ...previous,
        ...Object.fromEntries(persisted.map((item) => [
          item.documentId, { ...previous[item.documentId], status: item.layoutStatus },
        ])),
      }));
      setErrors((previous) => ({
        ...previous,
        ...Object.fromEntries(items.map((item) => [
          item.documentId, item.state === 'failed' ? layoutError('解析失败', item.reason) : '',
        ])),
      }));
      setStorageMessage('');
      const generated = persisted.filter((item) => item.state === 'saved').length;
      const reused = persisted.length - generated;
      const failed = items.length - persisted.length;
      const needsReview = persisted.filter((item) => item.layoutStatus !== 'completed').length;
      setBatchMessage(items.length
        ? `本次保存 ${generated} 份，复用 ${reused} 份，失败 ${failed} 份${needsReview ? `，${needsReview} 份已保存结果需复核` : ''}。`
        : '暂无可解析的已获取 PDF。');
    } catch (cause) {
      if (!controller.signal.aborted) setBatchMessage(`一键解析失败：${errorMessage(cause)}`);
    } finally {
      finish(controller);
    }
  }

  const eligible = sources.filter((source) => Object.hasOwn(schemas, source.type)
    && (!source.mimeType || source.mimeType === 'application/pdf'));
  const selectedSource = eligible.find((source) => source.documentId === selectedId);
  const selected = selectedSource ? saved[selectedSource.documentId]?.layout : undefined;
  const selectedLabel = materials.find((material) => material.type === selectedSource?.type)?.label;

  return (
    <div className="document-parsing" role="region" aria-labelledby={titleId}>
      <div className="parse-toolbar">
        <h4 id={titleId}>资料解析</h4>
        {eligible.length > 0 && (
          <button type="button" aria-disabled={!!busy} onClick={() => void parseAll()}>
            {busy?.kind === 'batch' ? '一键解析中…' : '一键解析'}
          </button>
        )}
      </div>
      <div role="status">{batchMessage}</div>
      {storageMessage && <p className="error">{storageMessage}</p>}
      <ul className="parse-materials">
        {materials.map(({ type, label, status }) => {
          if (!Object.hasOwn(schemas, type)) return null;
          const documents = eligible.filter((source) => source.type === type);
          return (
            <li key={type}>
              <strong>{label}</strong>
              {!documents.length && <span className="muted">{unavailableLabels[status] ?? '尚无可解析资料'}</span>}
              {documents.map(({ documentId }) => {
                const result = saved[documentId];
                const running = busy?.id === documentId && busy.kind === 'parse';
                const name = documents.length > 1 ? `${label} #${documentId}` : label;
                return (
                  <div className="parse-document" key={documentId}>
                    {documents.length > 1 && <span className="muted">#{documentId}</span>}
                    {result && (
                      <button type="button" className="secondary" aria-label={`查看${name}解析结果`}
                        aria-pressed={selectedId === documentId} aria-disabled={!!busy}
                        onClick={() => void openLayout(documentId, type, name, false)}>
                        查看结果
                      </button>
                    )}
                    <button type="button" className={result ? 'secondary' : undefined}
                      aria-label={`${running ? '正在解析' : result || errors[documentId] ? '重新解析' : '解析'}${name}`}
                      aria-disabled={!!busy}
                      onClick={() => void openLayout(documentId, type, name, true)}>
                      {running ? '解析中…' : result || errors[documentId] ? '重新解析' : '开始解析'}
                    </button>
                    {result && <span className="muted">{result.layout ? resultStatus(result.layout)
                      : storedStatusLabels[result.status ?? ''] ?? '解析结果已保存'}</span>}
                    {errors[documentId] && <span className="error">{errors[documentId]}</span>}
                  </div>
                );
              })}
            </li>
          );
        })}
      </ul>
      <div aria-live="polite" className="parse-visually-hidden">{announcement}</div>
      {selected && selectedSource && (
        <LayoutPreview key={selectedSource.documentId} layout={selected}
          label={selectedLabel ?? selectedSource.type} onPreview={() => onPreview(selectedSource.documentId)} />
      )}
    </div>
  );
}
