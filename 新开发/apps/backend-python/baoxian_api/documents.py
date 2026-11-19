import asyncio
import json
import logging
from weakref import WeakValueDictionary

from .companies import MySQLCompanyService
from .products import ProductNotFoundError
from .tasks import public_row


class DocumentNotFoundError(Exception): pass
class DocumentSourceNotFoundError(Exception): pass
class UnsupportedNumericDocumentError(Exception): pass
class NumericLayoutError(Exception): pass
class UnsupportedTextDocumentError(Exception): pass
class TextLayoutError(Exception): pass
class SavedLayoutNotFoundError(Exception): pass
class UnsupportedLayoutDocumentError(Exception): pass
class LayoutStorageError(Exception): pass


NUMERIC_LAYOUT_TYPES = {'rate_table': 'premium_rate', 'cash_value_table': 'cash_value'}
TEXT_LAYOUT_TYPES = {'clause': 'terms', 'prospectus': 'brochure'}
LAYOUT_TYPES = NUMERIC_LAYOUT_TYPES | TEXT_LAYOUT_TYPES
LAYOUT_STORAGE_VERSION = 'v1'
DOCUMENT_FIELDS = 'id,type,mime_type,sha256,storage_key'
logger = logging.getLogger(__name__)


def _layout_key(row):
    return f'layouts/{LAYOUT_STORAGE_VERSION}/{row["id"]}/{row["type"]}/{row["sha256"]}.json'


def _layout_status(layout):
    if 'status' in layout:
        return layout['status']
    tables = layout['tables']
    if not tables or any(table['structure_status'] != 'complete'
                         or table.get('continuation_status') == 'ambiguous' for table in tables):
        return 'completed_with_warnings'
    return 'completed'


def _parse_numeric_layout(body, document_id, document_type, digest):
    from pdfminer.pdfexceptions import PDFException
    from pypdf.errors import PyPdfError

    from .numeric_pdf.common import stable_hash
    from .numeric_pdf.compact_native import extract_mapping_input

    version = stable_hash(['document-version-v1', str(document_id), digest])
    try:
        return extract_mapping_input(body, version, document_type, expected_source_sha256=digest)
    except ValueError as error:
        reason = str(error)
        if reason not in (
            'file_hash_mismatch', 'encrypted_pdf_not_supported', 'compact_native_page_count_invalid',
            'pdf_page_count_mismatch', 'compact_native_page_needs_ocr',
            'invalid_temporary_cell_span', 'overlapping_temporary_cells',
        ):
            reason = 'invalid_pdf_layout'
        raise NumericLayoutError(reason) from error
    except (PyPdfError, PDFException) as error:
        raise NumericLayoutError('invalid_pdf') from error


def _parse_text_layout(body, document_id, document_type, digest):
    from pdfminer.pdfexceptions import PDFException
    from pypdf.errors import PyPdfError

    from .numeric_pdf.text_document import parse_text_document

    try:
        return parse_text_document(body, document_id, document_type, digest)
    except (PyPdfError, PDFException) as error:
        raise TextLayoutError('invalid_pdf') from error
    except ValueError as error:
        raise TextLayoutError(str(error)) from error


class DocumentService:
    def __init__(self, product_database_url, crawl_database_url, storage):
        self.product = MySQLCompanyService(product_database_url)
        self.crawl = MySQLCompanyService(crawl_database_url)
        self.storage = storage
        self._layout_locks = WeakValueDictionary()

    def _query(self, database, sql, args):
        with database._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
                return cursor.fetchall()

    async def get_file(self, document_id):
        rows = await asyncio.to_thread(self._query, self.product, 'SELECT id,storage_key,mime_type,filename FROM document WHERE id=%s', (document_id,))
        if not rows: raise DocumentNotFoundError(document_id)
        row = rows[0]
        obj = await self.storage.get(row['storage_key'])
        return {'body': obj['body'], 'contentType': row['mime_type'], 'filename': row['filename'] or f'document-{document_id}.pdf'}

    async def _layout_document(self, document_id):
        sql = f'SELECT {DOCUMENT_FIELDS} FROM document WHERE id=%s'
        rows = await asyncio.to_thread(self._query, self.product, sql, (document_id,))
        if not rows:
            raise DocumentNotFoundError(document_id)
        return rows[0]

    async def _extract_layout(self, row, parser, document_type):
        obj = await self.storage.get(row['storage_key'])
        return await asyncio.to_thread(parser, obj['body'], row['id'], document_type, row['sha256'])

    async def get_numeric_layout(self, document_id):
        row = await self._layout_document(document_id)
        if row['type'] not in NUMERIC_LAYOUT_TYPES or row['mime_type'] != 'application/pdf':
            raise UnsupportedNumericDocumentError(document_id)
        return await self._extract_layout(row, _parse_numeric_layout, NUMERIC_LAYOUT_TYPES[row['type']])

    async def get_parsed_content(self, document_id):
        row = await self._layout_document(document_id)
        if row['type'] not in TEXT_LAYOUT_TYPES or row['mime_type'] != 'application/pdf':
            raise UnsupportedTextDocumentError(document_id)
        return await self._extract_layout(row, _parse_text_layout, TEXT_LAYOUT_TYPES[row['type']])

    async def _current_layout_documents(self, product_id):
        fields = ','.join('d.' + field for field in DOCUMENT_FIELDS.split(','))
        marks = ','.join(['%s'] * len(LAYOUT_TYPES))
        sql = (f'SELECT {fields} FROM product p LEFT JOIN document d ON d.product_id=p.id '
               f"AND d.link_status='current' AND d.mime_type='application/pdf' AND d.type IN ({marks}) "
               'WHERE p.id=%s ORDER BY d.id')
        rows = await asyncio.to_thread(self._query, self.product, sql, (*LAYOUT_TYPES, product_id))
        if not rows:
            raise ProductNotFoundError(product_id)
        return [row for row in rows if row['id'] is not None]

    async def _storage_call(self, method, *args):
        try:
            return await method(*args)
        except Exception as error:
            logger.exception('Layout object operation failed: %s', args[0])
            raise LayoutStorageError('对象存储暂不可用或无法读写解析结果') from error

    async def list_saved_layouts(self, product_id):
        rows = await self._current_layout_documents(product_id)
        return [{'documentId': row['id'], 'type': row['type']} for row in rows
                if await self._storage_call(self.storage.exists, _layout_key(row))]

    async def _read_saved_layout(self, key):
        obj = await self._storage_call(self.storage.get, key)
        return obj['body']

    async def get_saved_layout(self, document_id):
        row = await self._layout_document(document_id)
        key = _layout_key(row)
        if not await self._storage_call(self.storage.exists, key):
            raise SavedLayoutNotFoundError(document_id)
        return await self._read_saved_layout(key)

    async def _parse_and_save(self, row, force, summarize=False):
        kind = row['type']
        if kind not in LAYOUT_TYPES or row['mime_type'] != 'application/pdf':
            raise UnsupportedLayoutDocumentError(row['id'])
        key = _layout_key(row)
        lock = self._layout_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force and await self._storage_call(self.storage.exists, key):
                body = await self._read_saved_layout(key)
                status = _layout_status(await asyncio.to_thread(json.loads, body)) if summarize else None
                return 'reused', body, status
            source = await self._storage_call(self.storage.get, row['storage_key'])
            parser = _parse_numeric_layout if kind in NUMERIC_LAYOUT_TYPES else _parse_text_layout
            layout = await asyncio.to_thread(parser, source['body'], row['id'], LAYOUT_TYPES[kind], row['sha256'])
            body = await asyncio.to_thread(lambda: json.dumps(layout, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            # Keep a readable result if a reparse cannot extract any pages.
            if force and layout.get('status') == 'failed' and await self._storage_call(self.storage.exists, key):
                existing = await self._read_saved_layout(key)
                if (await asyncio.to_thread(json.loads, existing)).get('status') != 'failed':
                    raise TextLayoutError('本次未能解析任何页面，已保留之前的结果')
            await self._storage_call(self.storage.put, key, body, 'application/json')
            return 'saved', body, _layout_status(layout) if summarize else None

    async def parse_document(self, document_id, force=False):
        row = await self._layout_document(document_id)
        _, body, _ = await self._parse_and_save(row, force)
        return body

    async def parse_product_documents(self, product_id):
        items = []
        for row in await self._current_layout_documents(product_id):
            item = {'documentId': row['id'], 'type': row['type']}
            try:
                state, _, status = await self._parse_and_save(row, force=False, summarize=True)
                item.update(state=state, layoutStatus=status)
            except (NumericLayoutError, TextLayoutError, LayoutStorageError) as error:
                item.update(state='failed', reason=str(error))
            except Exception:
                logger.exception('Could not parse document %s', row['id'])
                item.update(state='failed', reason='解析失败，请重试')
            items.append(item)
        return items

    async def list_sources(self, document_id):
        sql = 'SELECT id,document_id,task_id,snapshot_kind,source_url,download_url,snapshot_key,page_title,supporting_text,fetched_at FROM document_snapshot WHERE document_id=%s'
        return [public_row(row) for row in await asyncio.to_thread(self._query, self.crawl, sql, (document_id,))]

    async def list_task_snapshots(self, task_id):
        sql = 'SELECT id,document_id,task_id,snapshot_kind,source_url,download_url,snapshot_key,page_title,supporting_text,fetched_at FROM document_snapshot WHERE task_id=%s ORDER BY id DESC'
        return [public_row(row) for row in await asyncio.to_thread(self._query, self.crawl, sql, (task_id,))]

    async def get_snapshot(self, source_id):
        rows = await asyncio.to_thread(self._query, self.crawl, 'SELECT snapshot_key FROM document_snapshot WHERE id=%s', (source_id,))
        if not rows: raise DocumentSourceNotFoundError(source_id)
        obj = await self.storage.get(rows[0]['snapshot_key'])
        return obj['body'].decode('utf-8', errors='replace')

    async def list_failures(self, task_id=None, company_id=None, product_id=None, limit=100):
        filters = [('task_id', task_id), ('company_id', company_id), ('product_id', product_id)]
        conditions = [(column, value) for column, value in filters if value is not None]
        sql = 'SELECT * FROM document_failure'
        if conditions:
            sql += ' WHERE ' + ' AND '.join(column + '=%s' for column, _ in conditions)
        sql += ' ORDER BY id DESC LIMIT %s'
        rows = await asyncio.to_thread(self._query, self.crawl, sql, tuple(value for _, value in conditions) + (limit,))
        async def names(table, ids):
            if not ids: return {}
            sql = 'SELECT id,name FROM ' + table + ' WHERE id IN (' + ','.join(['%s'] * len(ids)) + ')'
            values = await asyncio.to_thread(self._query, self.product, sql, tuple(ids))
            return {row['id']: row['name'] for row in values}
        company_ids = {row['company_id'] for row in rows if row['company_id'] is not None}
        product_ids = {row['product_id'] for row in rows if row['product_id'] is not None}
        companies, products = await asyncio.gather(names('company', company_ids), names('product', product_ids))
        result = []
        for row in rows:
            item = public_row(row)
            item['companyName'] = companies.get(row['company_id'])
            item['productName'] = products.get(row['product_id'])
            result.append(item)
        return result

    async def p2_report(self, company_id=None, product_id=None):
        conditions, args = [], []
        if company_id is not None: conditions.append('p.company_id=%s'); args.append(company_id)
        if product_id is not None: conditions.append('p.id=%s'); args.append(product_id)
        sql = 'SELECT p.id,p.company_id,c.name AS company_name FROM product p JOIN company c ON c.id=p.company_id'
        if conditions: sql += ' WHERE ' + ' AND '.join(conditions)
        products = await asyncio.to_thread(self._query, self.product, sql, tuple(args))
        totals = {'products': len(products), 'documents': 0, 'links': 0, 'failures': 0}
        if not products: return {'companies': [], 'totals': totals}
        product_ids = tuple(row['id'] for row in products)
        company_ids = tuple({row['company_id'] for row in products})
        pmarks = ','.join(['%s'] * len(product_ids))
        cmarks = ','.join(['%s'] * len(company_ids))
        docs, statuses, failures = await asyncio.gather(
            asyncio.to_thread(self._query, self.product, "SELECT id,product_id,type FROM document WHERE link_status='current' AND product_id IN (" + pmarks + ')', product_ids),
            asyncio.to_thread(self._query, self.crawl, 'SELECT product_id,type,status FROM product_document_status WHERE product_id IN (' + pmarks + ')', product_ids),
            asyncio.to_thread(self._query, self.crawl, 'SELECT company_id,product_id,reason FROM document_failure WHERE company_id IN (' + cmarks + ')' + (' AND product_id=%s' if product_id is not None else ''), company_ids + ((product_id,) if product_id is not None else ())),
        )
        linked = {(row['product_id'], row['type']) for row in docs}
        status_map = {(row['product_id'], row['type']): row['status'] for row in statuses}
        reports = []
        for cid in sorted(company_ids):
            own = [row for row in products if row['company_id'] == cid]
            ids = {row['id'] for row in own}
            own_docs = [row for row in docs if row['product_id'] in ids]
            own_failures = [row for row in failures if row['company_id'] == cid]
            failure_counts = {}
            for failure in own_failures:
                reason = failure['reason']
                failure_counts[reason] = failure_counts.get(reason, 0) + 1
            materials = {}
            for kind in ('clause', 'rate_table', 'cash_value_table', 'prospectus'):
                counts = {}
                for pid in ids:
                    key = (pid, kind)
                    state = 'downloaded' if key in linked else status_map.get(key, 'not_run')
                    counts[state] = counts.get(state, 0) + 1
                materials[kind] = counts
            reports.append({'companyId': cid, 'companyName': own[0]['company_name'], 'products': len(own), 'documents': len({row['id'] for row in own_docs}), 'links': len(own_docs), 'failures': failure_counts, 'materials': materials})
        totals.update({'documents': len({row['id'] for row in docs}), 'links': len(docs), 'failures': len(failures)})
        return {'companies': reports, 'totals': totals}
