import DocumentParsingPanel from './DocumentParsingPanel';

interface ProductMaterial {
  type: string;
  label: string;
  status: string;
}

interface ProductSource {
  id: number;
  documentId: number;
  type: string;
  mimeType?: string;
  sourceUrl: string;
  downloadUrl: string;
  fetchedAt: string;
}

interface AssociationRecord {
  associationRecordId: string | null;
  salesStatus: string;
  salesStatusRaw: string | null;
  saleDateRaw: string | null;
  statusFetchedAt: string;
  sourceUrl: string;
  categoryName: string;
  officialProductCode: string | null;
  filingNumber: string | null;
}

export interface ProductDetailData {
  id: number;
  companyName: string;
  name: string;
  sameNameCount: number;
  identityGroup: string;
  salesStatus: string;
  salesStatusRaw: string | null;
  saleDateRaw: string | null;
  statusFetchedAt: string;
  categoryName: string;
  officialProductUrl: string | null;
  officialProductCode: string | null;
  filingNumber: string | null;
  sourceUrl: string;
  confirmedName: string | null;
  confirmedSalesStatus: string | null;
  correctionNote: string | null;
  confirmedBy: string | null;
  confirmedAt: string | null;
  integrationStatus: string;
  integrationStatusReason: string | null;
  integrationStatusUpdatedAt: string | null;
  materials: ProductMaterial[];
  sources: ProductSource[];
  history: ProductSource[];
  associationRecords: AssociationRecord[];
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

import { formatBeijingTime as formatTime } from './time';

export default function ProductDetailPanel({
  detail,
  token,
  onPreviewDocument,
  onPreviewSnapshot,
}: {
  detail: ProductDetailData;
  token: string;
  onPreviewDocument: (documentId: number) => void;
  onPreviewSnapshot: (sourceId: number) => void;
}) {
  return (
    <div className="detail">
      <h3>
        {detail.companyName} · {detail.name}
      </h3>
      <p className="muted">
        销售状态：{statusLabels[detail.salesStatus] ?? detail.salesStatus} ·
        获取时间（北京时间）：{formatTime(detail.statusFetchedAt)}
      </p>
      <p className="muted">销售时间（来源）：{detail.saleDateRaw || '尚未获取'}</p>
      <p className="muted">同名协会记录：{detail.sameNameCount} 条</p>
      <p className="muted">
        接入状态：
        {integrationStatusLabels[detail.integrationStatus] ??
          detail.integrationStatus}
        {detail.integrationStatusReason
          ? ` · ${detail.integrationStatusReason}`
          : ''}
        {detail.integrationStatusUpdatedAt
          ? ` · ${formatTime(detail.integrationStatusUpdatedAt)}`
          : ''}
      </p>
      <p className="muted">
        类别：{detail.categoryName} · 官网产品页：
        {detail.officialProductUrl ? (
          <a href={detail.officialProductUrl} target="_blank" rel="noreferrer">
            打开官网产品页
          </a>
        ) : (
          '待补'
        )}
        · 协会索引来源：
        <a href={detail.sourceUrl} target="_blank" rel="noreferrer">
          打开索引来源
        </a>
        · 官网代码：{detail.officialProductCode ?? '—'} · 备案号：
        {detail.filingNumber ?? '—'}
      </p>

      {(detail.confirmedName ||
        detail.confirmedSalesStatus ||
        detail.correctionNote) && (
        <p className="muted">
          人工确认：{detail.confirmedName ?? '名称未确认'} /{' '}
          {detail.confirmedSalesStatus
            ? (statusLabels[detail.confirmedSalesStatus] ??
              detail.confirmedSalesStatus)
            : '状态未确认'}
          {detail.correctionNote ? ` · ${detail.correctionNote}` : ''}
          {detail.confirmedBy ? ` · 操作者 ${detail.confirmedBy}` : ''}
          {detail.confirmedAt ? ` · ${formatTime(detail.confirmedAt)}` : ''}
        </p>
      )}

      <h4>协会产品记录</h4>
      {detail.associationRecords.length === 0 ? (
        <p className="muted">尚无协会记录</p>
      ) : (
        <table>
          <thead><tr><th>协会记录 ID</th><th>销售状态</th><th>销售时间</th><th>类别</th><th>官网代码 / 备案号</th><th>来源</th></tr></thead>
          <tbody>{detail.associationRecords.map((record, index) => (
            <tr key={record.associationRecordId ?? `${record.categoryName}-${record.saleDateRaw}-${index}`}>
              <td>{record.associationRecordId ?? '未提供'}</td>
              <td>{record.salesStatusRaw || statusLabels[record.salesStatus] || record.salesStatus}</td>
              <td>{record.saleDateRaw || '未提供'}</td>
              <td>{record.categoryName}</td>
              <td>{record.officialProductCode ?? '—'} / {record.filingNumber ?? '—'}</td>
              <td><a href={record.sourceUrl} target="_blank" rel="noreferrer">协会索引</a></td>
            </tr>
          ))}</tbody>
        </table>
      )}

      <h4>产品资料</h4>
      <table>
        <thead>
          <tr>
            <th>资料类型</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {detail.materials.map((material) => (
            <tr key={material.type}>
              <td>{material.label}</td>
              <td>{materialStatusLabels[material.status] ?? material.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <DocumentParsingPanel
        key={token}
        productId={detail.id}
        token={token}
        materials={detail.materials}
        sources={detail.sources}
        onPreview={onPreviewDocument}
      />

      <h4>资料来源</h4>
      {detail.sources.length === 0 ? (
        <p className="muted">尚无来源记录</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>类型</th>
              <th>版本</th>
              <th>确认</th>
              <th>官网来源页</th>
              <th>获取时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {detail.sources.map((source) => (
              <tr key={source.id}>
                <td>{source.type}</td>
                <td>
                  {source.sourceUrl ? <a href={source.sourceUrl} target="_blank" rel="noreferrer">官网页面</a> : '待确认'}
                </td>
                <td>{formatTime(source.fetchedAt)}</td>
                <td>
                  <div className="actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => onPreviewDocument(source.documentId)}
                    >
                      预览原件
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={!source.id}
                      onClick={() => onPreviewSnapshot(source.id)}
                    >
                      来源快照
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>历史资料</h4>
      <p className="muted">
        {detail.history.length === 0
          ? '尚无历史资料'
          : `已有 ${detail.history.length} 条历史记录`}
      </p>
    </div>
  );
}
