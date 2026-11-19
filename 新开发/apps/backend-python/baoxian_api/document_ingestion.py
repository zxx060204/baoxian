import asyncio
import hashlib
import json

import pymysql
from urllib.parse import urlsplit

from .companies import MySQLCompanyService
from .database_helpers import qualified_database


class DocumentIngestionService:
    def __init__(self, product_database_url, crawl_database_url, storage, download):
        self.product = MySQLCompanyService(product_database_url)
        self.crawl = MySQLCompanyService(crawl_database_url)
        self.storage = storage
        self.download = download

    def _find_document(self, digest, kind, product_id):
        with self.product._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM document WHERE sha256=%s AND type=%s AND product_id <=> %s LIMIT 1', (digest, kind, product_id))
                return cursor.fetchone()

    def _insert_document(self, digest, storage_key, downloaded, candidate, product_id):
        filename = urlsplit(candidate['downloadUrl']).path.rsplit('/', 1)[-1] or None
        with self.product._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO document (product_id,type,link_status,sha256,filename,mime_type,size,storage_key,source_url,download_url,page_title) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (product_id, candidate['type'], 'historical' if candidate.get('versionStatus') == 'historical' else 'current', digest, filename, downloaded['contentType'], len(downloaded['body']), storage_key, candidate.get('officialProductUrl'), candidate['downloadUrl'], candidate['title']))
                document_id = cursor.lastrowid
                cursor.execute('SELECT * FROM document WHERE id=%s', (document_id,))
                return cursor.fetchone()

    def _snapshot(self, task_id, document_id, candidate, snapshot_key):
        with self.crawl._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO document_snapshot (task_id,document_id,product_ids,source_url,download_url,snapshot_key,snapshot_kind,page_title,supporting_text,fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())', (task_id, document_id, json.dumps(candidate['productIds']), candidate['sourcePageUrl'], candidate['downloadUrl'], snapshot_key, candidate.get('snapshotKind', 'webpage'), candidate['title'], candidate.get('sourceText')))
                return cursor.lastrowid

    def _link_existing(self, product_id, document_id, candidate, task_id):
        historical = candidate.get('versionStatus') == 'historical'
        with self.product._connect() as connection:
            with connection.cursor() as cursor:
                if candidate.get('officialProductUrl'):
                    parsed = urlsplit(candidate['officialProductUrl'])
                    if parsed.scheme and parsed.netloc and (parsed.path not in ('', '/') or parsed.query):
                        cursor.execute('UPDATE product SET official_product_url=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (candidate['officialProductUrl'], product_id))
                if not historical:
                    cursor.execute("UPDATE document SET link_status='historical',updated_at=UTC_TIMESTAMP() WHERE product_id=%s AND type=%s AND download_url=%s AND id<>%s", (product_id, candidate['type'], candidate['downloadUrl'], document_id))
                cursor.execute('UPDATE document SET link_status=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', ('historical' if historical else 'current', document_id))
        if not historical:
            with self.crawl._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO product_document_status (product_id,type,status,task_id) VALUES (%s,%s,'downloaded',%s) ON DUPLICATE KEY UPDATE status='downloaded',reason=NULL,task_id=VALUES(task_id),updated_at=UTC_TIMESTAMP()", (product_id, candidate['type'], task_id))

    def _record_failure(self, company_id, product_id, task_id, candidate, reason, message):
        with self.crawl._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO document_failure (task_id,company_id,product_id,type,title,download_url,source_page_url,reason,message) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (task_id, company_id, product_id, candidate['type'], candidate['title'], candidate['downloadUrl'], candidate['sourcePageUrl'], reason, message))
                if product_id is not None:
                    cursor.execute("INSERT INTO product_document_status (product_id,type,status,reason,task_id) VALUES (%s,%s,'download_failed',%s,%s) ON DUPLICATE KEY UPDATE status='download_failed',reason=VALUES(reason),task_id=VALUES(task_id),updated_at=UTC_TIMESTAMP()", (product_id, candidate['type'], f'{reason}: {message}', task_id))

    async def ingest_candidate(self, company_id, candidate, *, product_id=None, task_id=None, retry_attempts=1, request_timeout_ms=30000):
        downloaded = await self.download(candidate['downloadUrl'], timeout_ms=request_timeout_ms)
        for _ in range(1, max(1, retry_attempts)):
            if downloaded['ok'] or downloaded['reason'] in ('cancelled', 'format', 'empty', 'too_large'):
                break
            downloaded = await self.download(candidate['downloadUrl'], timeout_ms=request_timeout_ms)
        if not downloaded['ok']:
            if downloaded['reason'] != 'cancelled':
                await asyncio.to_thread(self._record_failure, company_id, product_id, task_id, candidate, downloaded['reason'], downloaded['message'])
            return {'status': 'failed', 'reason': downloaded['reason'], 'message': downloaded['message']}
        digest = hashlib.sha256(downloaded['body']).hexdigest()
        key = f'documents/{digest}.pdf'
        lookup_product_id = product_id if product_id is not None else (candidate['productIds'][0] if candidate.get('shared') and candidate.get('productIds') else None)
        document = await asyncio.to_thread(self._find_document, digest, candidate['type'], lookup_product_id)
        if document is None:
            await self.storage.put(key, downloaded['body'], downloaded['contentType'])
            try:
                document = await asyncio.to_thread(self._insert_document, digest, key, downloaded, candidate, product_id)
            except pymysql.IntegrityError as error:
                if error.args[0] != 1062:
                    raise
                document = await asyncio.to_thread(self._find_document, digest, candidate['type'], product_id)
                if document is None:
                    raise
        if not document['source_url'] and candidate.get('officialProductUrl'):
            def update_source():
                with self.product._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('UPDATE document SET source_url=%s WHERE id=%s', (candidate['officialProductUrl'], document['id']))
            await asyncio.to_thread(update_source)
        snapshot_body = candidate['snapshotHtml'].encode('utf-8')
        snapshot_key = f"snapshots/{hashlib.sha256(snapshot_body).hexdigest()}.html"
        await self.storage.put(snapshot_key, snapshot_body, 'text/html; charset=utf-8')
        source_id = await asyncio.to_thread(self._snapshot, task_id, document['id'], candidate, snapshot_key)
        if product_id is not None:
            await asyncio.to_thread(self._link_existing, product_id, document['id'], candidate, task_id)
        return {'status': 'stored', 'documentId': document['id'], 'sourceId': source_id}

    async def link_to_product(self, product_id, document_id, source_id, candidate, *, task_id=None):
        def link():
            with self.product._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT * FROM document WHERE id=%s', (document_id,))
                    original = cursor.fetchone()
                    if original is None:
                        raise ValueError('文件不存在')
                    cursor.execute('SELECT id FROM document WHERE product_id=%s AND type=%s AND sha256=%s LIMIT 1', (product_id, candidate['type'], original['sha256']))
                    existing = cursor.fetchone()
                    actual_id = existing['id'] if existing else None
                    if actual_id is None and original['product_id'] is None:
                        cursor.execute('UPDATE document SET product_id=%s,type=%s,link_status=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s AND product_id IS NULL', (product_id, candidate['type'], 'historical' if candidate.get('versionStatus') == 'historical' else 'current', document_id))
                        if cursor.rowcount:
                            actual_id = document_id
                    if actual_id is None:
                        cursor.execute('INSERT INTO document (product_id,type,link_status,sha256,filename,mime_type,size,storage_key,source_url,download_url,page_title) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (product_id, candidate['type'], 'historical' if candidate.get('versionStatus') == 'historical' else 'current', original['sha256'], original['filename'], original['mime_type'], original['size'], original['storage_key'], candidate.get('officialProductUrl') or original['source_url'], candidate['downloadUrl'], candidate['title']))
                        actual_id = cursor.lastrowid
            if actual_id != document_id:
                with self.crawl._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT * FROM document_snapshot WHERE id=%s', (source_id,))
                        snapshot = cursor.fetchone()
                        if snapshot:
                            cursor.execute('INSERT INTO document_snapshot (task_id,document_id,product_ids,source_url,download_url,snapshot_key,snapshot_kind,page_title,supporting_text,fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (snapshot['task_id'], actual_id, json.dumps([product_id]), snapshot['source_url'], snapshot['download_url'], snapshot['snapshot_key'], snapshot['snapshot_kind'], snapshot['page_title'], snapshot['supporting_text'], snapshot['fetched_at']))
            self._link_existing(product_id, actual_id, candidate, task_id)
        await asyncio.to_thread(link)

    def _mark_status(self, product_id, kind, status, task_id, reason=None):
        with self.crawl._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM product_document_status WHERE product_id=%s AND type=%s LIMIT 1', (product_id, kind))
                existing = cursor.fetchone()
                if existing and (existing['status'] == 'downloaded' or status == 'not_found' and existing['task_id'] == task_id):
                    return
                if status == 'attribution_pending' and existing:
                    cursor.execute("UPDATE product_document_status SET status='attribution_pending',task_id=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s", (task_id, existing['id']))
                else:
                    cursor.execute('INSERT INTO product_document_status (product_id,type,status,reason,task_id) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status),reason=VALUES(reason),task_id=VALUES(task_id),updated_at=UTC_TIMESTAMP()', (product_id, kind, status, reason, task_id))

    async def mark_not_found(self, product_id, kind, task_id=None):
        await asyncio.to_thread(self._mark_status, product_id, kind, 'not_found', task_id)

    async def mark_download_failed(self, product_id, kind, task_id=None, reason=None):
        await asyncio.to_thread(self._mark_status, product_id, kind, 'download_failed', task_id, reason)

    async def mark_attribution_pending(self, product_id, kind, task_id=None):
        await asyncio.to_thread(self._mark_status, product_id, kind, 'attribution_pending', task_id)

    async def set_product_integration_status(self, product_id, status, task_id=None, reason=None):
        def update():
            nonlocal status, reason
            with self.product._connect() as connection:
                with connection.cursor() as cursor:
                    if status in ('completed', 'partial_failed'):
                        cursor.execute("SELECT DISTINCT type FROM document WHERE product_id=%s AND link_status='current'", (product_id,))
                        present = {row['type'] for row in cursor.fetchall()}
                        complete = all(kind in present for kind in ('clause', 'rate_table', 'cash_value_table', 'prospectus'))
                        status = 'completed' if complete else 'partial_failed'
                        if not complete and not reason:
                            reason = '四类资料尚未齐全'
                    cursor.execute('UPDATE product SET integration_status=%s,integration_status_reason=%s,integration_last_task_id=%s,integration_status_updated_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s', (status, reason, task_id, product_id))
        await asyncio.to_thread(update)

    async def begin_product_integration(self, product_id, task_id):
        def begin():
            crawl_database = qualified_database(self.product, self.crawl)
            with self.product._connect() as connection:
                try:
                    connection.begin()
                    with connection.cursor() as cursor:
                        cursor.execute(f'SELECT status,scope FROM {crawl_database}.crawl_task WHERE id=%s FOR UPDATE', (task_id,))
                        task = cursor.fetchone()
                        if task is None or task['status'] in ('cancelled', 'completed'):
                            connection.rollback()
                            return None
                        cursor.execute('SELECT integration_status,integration_status_reason,integration_last_task_id,integration_status_updated_at FROM product WHERE id=%s FOR UPDATE', (product_id,))
                        previous = cursor.fetchone()
                        if previous is not None:
                            scope = json.loads(task['scope']) if isinstance(task['scope'], str) else task['scope']
                            snapshots = scope.setdefault('_integrationBefore', {})
                            key = str(product_id)
                            if previous['integration_status'] == 'running' and previous['integration_last_task_id'] == task_id and key in snapshots:
                                previous = snapshots[key]
                            else:
                                previous = dict(previous)
                                timestamp = previous['integration_status_updated_at']
                                if timestamp is not None: previous['integration_status_updated_at'] = str(timestamp)
                                snapshots[key] = previous
                            cursor.execute(f'UPDATE {crawl_database}.crawl_task SET scope=%s WHERE id=%s', (json.dumps(scope, ensure_ascii=False), task_id))
                            cursor.execute("UPDATE product SET integration_status='running',integration_status_reason=NULL,integration_last_task_id=%s,integration_status_updated_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s", (task_id, product_id))
                    connection.commit()
                    return previous
                except BaseException:
                    connection.rollback()
                    raise
        return await asyncio.to_thread(begin)

    async def restore_product_integration(self, product_id, task_id, previous):
        def restore():
            with self.product._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE product SET integration_status=%s,integration_status_reason=%s,integration_last_task_id=%s,integration_status_updated_at=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s AND integration_status='running' AND integration_last_task_id=%s", (previous['integration_status'], previous['integration_status_reason'], previous['integration_last_task_id'], previous['integration_status_updated_at'], product_id, task_id))
        await asyncio.to_thread(restore)
