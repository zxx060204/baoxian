import asyncio
import unittest
from uuid import uuid4

from test_company_api import test_database_url
from test_product_api import test_crawl_url
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.products import MySQLProductService
from baoxian_api.document_ingestion import DocumentIngestionService
from baoxian_api.documents import DocumentService
from baoxian_api.app import create_app
from fastapi.testclient import TestClient


class MemoryStorage:
    def __init__(self): self.objects = {}
    async def put(self, key, body, content_type): self.objects[key] = {'body': body, 'contentType': content_type}
    async def get(self, key): return self.objects[key]


class DocumentIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product = MySQLCompanyService(test_database_url())._connect()
        self.pc = self.product.cursor()
        self.crawl = MySQLCompanyService(test_crawl_url())._connect()
        self.cc = self.crawl.cursor()
        self.name = 'python-document-' + uuid4().hex
        self.pc.execute('INSERT INTO company (name) VALUES (%s)', (self.name,))
        self.company_id = self.pc.lastrowid
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, self.name, self.name))
        self.product_id = self.pc.lastrowid
        self.task_id = int(uuid4().int % 1000000000000) + 1000000000000
        self.storage = MemoryStorage()
        async def download(url, **kwargs): return {'ok': True, 'body': b'%PDF-example', 'contentType': 'application/pdf', 'finalUrl': url}
        self.service = DocumentIngestionService(test_database_url(), test_crawl_url(), self.storage, download)
        self.candidate = {'productIds': [self.product_id], 'type': 'clause', 'title': '保险条款', 'downloadUrl': 'https://example.test/' + self.name + '.pdf', 'sourcePageUrl': 'https://example.test/list', 'officialProductUrl': 'https://example.test/product/1', 'snapshotHtml': '<html>产品和条款</html>'}

    async def asyncTearDown(self):
        self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM document_failure WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (self.product_id,))
        self.pc.execute('DELETE FROM document WHERE product_id=%s OR (product_id IS NULL AND download_url=%s)', (self.product_id, self.candidate['downloadUrl']))
        self.pc.execute('DELETE FROM product WHERE company_id=%s AND name=%s', (self.company_id, self.name + '-other'))
        self.pc.execute('DELETE FROM product WHERE id=%s', (self.product_id,))
        self.pc.execute('DELETE FROM company WHERE id=%s AND name=%s', (self.company_id, self.name))
        self.cc.close(); self.crawl.close(); self.pc.close(); self.product.close()

    async def test_document_file_and_snapshot_http_endpoints(self):
        result = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        service = DocumentService(test_database_url(), test_crawl_url(), self.storage)
        with TestClient(create_app(internal_api_token='token', document_service=service)) as client:
            headers = {'Authorization': 'Bearer token'}
            self.assertEqual(client.get(f"/api/documents/{result['documentId']}/file").status_code, 401)
            file = client.get(f"/api/documents/{result['documentId']}/file", headers=headers)
            self.assertEqual((file.status_code, file.content, file.headers['content-type']), (200, b'%PDF-example', 'application/pdf'))
            self.assertTrue(file.headers['content-disposition'].startswith('inline; filename='))
            sources = client.get(f"/api/documents/{result['documentId']}/sources", headers=headers).json()['items']
            self.assertEqual((sources[0]['id'], sources[0]['snapshotKind']), (result['sourceId'], 'webpage'))
            snapshots = client.get(f'/api/crawl-tasks/{self.task_id}/document-snapshots', headers=headers).json()['items']
            self.assertEqual(snapshots[0]['id'], result['sourceId'])
            page = client.get(f"/api/document-sources/{result['sourceId']}/snapshot", headers=headers)
            self.assertIn('产品和条款', page.text)
            self.assertEqual(page.headers['content-type'], 'text/html; charset=utf-8')
            self.assertEqual(client.get('/api/documents/999999999999/file', headers=headers).status_code, 404)
            self.assertEqual(client.get('/api/document-sources/999999999999/snapshot', headers=headers).status_code, 404)

    async def test_real_minio_ingestion_and_http_file_snapshot(self):
        from baoxian_api.storage import MinioStorage
        from test_storage import minio_settings
        env = minio_settings()
        bucket = 'python-tdd-' + uuid4().hex
        storage = MinioStorage(env.get('MINIO_ENDPOINT', '127.0.0.1'), int(env.get('MINIO_PORT', '9000')), env.get('MINIO_USE_SSL') == 'true', env['MINIO_ACCESS_KEY'], env['MINIO_SECRET_KEY'], bucket)
        ingestion = DocumentIngestionService(test_database_url(), test_crawl_url(), storage, self.service.download)
        try:
            result = await ingestion.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
            service = DocumentService(test_database_url(), test_crawl_url(), storage)
            with TestClient(create_app(internal_api_token='token', document_service=service)) as client:
                headers = {'Authorization': 'Bearer token'}
                file = client.get(f"/api/documents/{result['documentId']}/file", headers=headers)
                page = client.get(f"/api/document-sources/{result['sourceId']}/snapshot", headers=headers)
                self.assertEqual((file.status_code, file.content, file.headers['content-type']), (200, b'%PDF-example', 'application/pdf'))
                self.assertEqual((page.status_code, page.text), (200, '<html>产品和条款</html>'))
        finally:
            def cleanup_bucket():
                if storage.client.bucket_exists(bucket):
                    for obj in storage.client.list_objects(bucket, recursive=True):
                        storage.client.remove_object(bucket, obj.object_name)
                    storage.client.remove_bucket(bucket)
            if bucket.startswith('python-tdd-'): await asyncio.to_thread(cleanup_bucket)

    async def test_failure_report_filters_and_enriches_results(self):
        async def fail(url, **kwargs): return {'ok': False, 'reason': 'http', 'message': 'HTTP 404'}
        self.service.download = fail
        await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        service = DocumentService(test_database_url(), test_crawl_url(), self.storage)
        with TestClient(create_app(internal_api_token='token', document_service=service)) as client:
            headers = {'Authorization': 'Bearer token'}
            query = {'taskId': self.task_id, 'companyId': self.company_id, 'productId': self.product_id, 'limit': 1}
            response = client.get('/api/reports/failures', headers=headers, params=query)
            self.assertEqual(response.status_code, 200)
            items = response.json()['items']
            self.assertEqual(len(items), 1)
            self.assertEqual((items[0]['reason'], items[0]['companyName'], items[0]['productName']), ('http', self.name, self.name))
            self.assertEqual((items[0]['downloadUrl'], items[0]['sourcePageUrl']), (self.candidate['downloadUrl'], self.candidate['sourcePageUrl']))
            self.cc.execute('SELECT MAX(id) AS id FROM document_failure WHERE task_id=%s', (self.task_id,))
            self.assertEqual(items[0]['id'], self.cc.fetchone()['id'])
            self.assertEqual(client.get('/api/reports/failures', headers=headers, params={'taskId': self.task_id, 'companyId': self.company_id + 100000000}).json()['items'], [])
            self.assertEqual(client.get('/api/reports/failures', headers=headers, params={'limit': 501}).status_code, 400)

    async def test_p2_report_counts_current_documents_and_material_status(self):
        await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        async def fail(url, **kwargs): return {'ok': False, 'reason': 'http', 'message': 'HTTP 404'}
        self.service.download = fail
        await self.service.ingest_candidate(self.company_id, {**self.candidate, 'type': 'rate_table'}, product_id=self.product_id, task_id=self.task_id)
        self.cc.execute("UPDATE product_document_status SET status='download_failed' WHERE product_id=%s AND type='clause'", (self.product_id,))
        other_name = self.name + '-other'
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, other_name, other_name))
        service = DocumentService(test_database_url(), test_crawl_url(), self.storage)
        with TestClient(create_app(internal_api_token='token', document_service=service)) as client:
            headers = {'Authorization': 'Bearer token'}
            response = client.get('/api/reports/p2', headers=headers, params={'companyId': self.company_id, 'productId': self.product_id})
            self.assertEqual(response.status_code, 200)
            report = response.json()
            self.assertEqual(report['totals'], {'products': 1, 'documents': 1, 'links': 1, 'failures': 1})
            company = report['companies'][0]
            self.assertEqual((company['companyName'], company['failures']['http']), (self.name, 1))
            self.assertEqual(company['materials']['clause']['downloaded'], 1)
            self.assertEqual(company['materials']['rate_table']['download_failed'], 1)
            self.assertEqual(company['materials']['prospectus']['not_run'], 1)
            company_only = client.get('/api/reports/p2', headers=headers, params={'companyId': self.company_id})
            product_only = client.get('/api/reports/p2', headers=headers, params={'productId': self.product_id})
            self.assertEqual(company_only.json()['totals']['products'], 2)
            self.assertEqual(product_only.json()['totals']['products'], 1)
            self.assertEqual(product_only.json()['companies'][0]['products'], 1)
            self.assertEqual(client.get('/api/reports/p2', headers=headers, params={'productId': 0}).status_code, 400)

    async def test_review_assigns_document_and_updates_snapshot(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        candidate = {**self.candidate, 'productIds': []}
        saved = await self.service.ingest_candidate(self.company_id, candidate, task_id=self.task_id)
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        item = reporter.record_document_attribution(self.task_id, candidate, saved['documentId'], saved['sourceId'])
        try:
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                headers = {'Authorization': 'Bearer token'}
                url = f"/api/review-items/{item['id']}/resolve"
                bad = client.post(url, headers=headers, json={'action': 'assign', 'productId': self.product_id + 1000000})
                self.assertEqual((bad.status_code, bad.json()['code']), (400, 'invalid_resolution'))
                result = client.post(url, headers=headers, json={'action': 'assign', 'productId': self.product_id})
                self.assertEqual((result.status_code, result.json()['item']['resolvedProductId']), (200, self.product_id))
                self.assertEqual(client.post(url, headers=headers, json={'action': 'assign', 'productId': self.product_id}).status_code, 409)
            self.pc.execute('SELECT product_id FROM document WHERE id=%s', (saved['documentId'],))
            self.assertEqual(self.pc.fetchone()['product_id'], self.product_id)
            self.cc.execute('SELECT product_ids FROM document_snapshot WHERE id=%s', (saved['sourceId'],))
            import json
            self.assertIn(self.product_id, json.loads(self.cc.fetchone()['product_ids']))
            self.cc.execute("SELECT status FROM product_document_status WHERE product_id=%s AND type='clause'", (self.product_id,))
            self.assertEqual(self.cc.fetchone()['status'], 'downloaded')
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))

    async def test_review_rejects_mismatched_snapshot_without_linking(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        candidate = {**self.candidate, 'productIds': []}
        saved = await self.service.ingest_candidate(self.company_id, candidate, task_id=self.task_id)
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        item = reporter.record_document_attribution(self.task_id, candidate, saved['documentId'], saved['sourceId'] + 100000000)
        try:
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                response = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'assign', 'productId': self.product_id})
                self.assertEqual((response.status_code, response.json()['code']), (400, 'invalid_resolution'))
            self.pc.execute('SELECT product_id FROM document WHERE id=%s', (saved['documentId'],))
            self.assertIsNone(self.pc.fetchone()['product_id'])
            self.cc.execute('SELECT status FROM review_item WHERE id=%s', (item['id'],))
            self.assertEqual(self.cc.fetchone()['status'], 'pending')
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))

    async def test_review_assigns_existing_document_to_another_product_as_historical(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        import json
        second_name = self.name + '-second'
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, second_name, second_name))
        second_id = self.pc.lastrowid
        saved = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        candidate = {**self.candidate, 'versionStatus': 'historical'}
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        item = reporter.record_document_attribution(self.task_id, candidate, saved['documentId'], saved['sourceId'])
        try:
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                response = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'assign', 'productId': second_id})
                self.assertEqual(response.status_code, 200)
            self.pc.execute('SELECT id,storage_key,link_status FROM document WHERE product_id IN (%s,%s) ORDER BY product_id', (self.product_id, second_id))
            docs = self.pc.fetchall()
            self.assertEqual(len(docs), 2)
            self.assertEqual(docs[0]['storage_key'], docs[1]['storage_key'])
            self.assertEqual((docs[0]['link_status'], docs[1]['link_status']), ('current', 'historical'))
            self.cc.execute('SELECT product_ids FROM document_snapshot WHERE id=%s', (saved['sourceId'],))
            self.assertIn(second_id, json.loads(self.cc.fetchone()['product_ids']))
            self.cc.execute('SELECT COUNT(*) AS n FROM document_snapshot WHERE task_id=%s AND document_id=%s', (self.task_id, docs[1]['id']))
            self.assertEqual(self.cc.fetchone()['n'], 1)
            self.cc.execute('SELECT COUNT(*) AS n FROM product_document_status WHERE product_id=%s', (second_id,))
            self.assertEqual(self.cc.fetchone()['n'], 0)
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))
            self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
            self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM document WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM product WHERE id=%s AND company_id=%s', (second_id, self.company_id))

    async def test_review_assignment_rolls_back_all_changes_on_failure(self):
        from unittest.mock import patch
        from baoxian_api.review_reporter import MySQLReviewReporter
        from baoxian_api.tasks import public_row
        import json
        candidate = {**self.candidate, 'productIds': []}
        saved = await self.service.ingest_candidate(self.company_id, candidate, task_id=self.task_id)
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        item = reporter.record_document_attribution(self.task_id, candidate, saved['documentId'], saved['sourceId'])
        def fail(row):
            if 'resolution' in row and row['resolution'] == 'assign': raise RuntimeError('故障注入')
            return public_row(row)
        try:
            with patch('baoxian_api.review_reporter.public_row', side_effect=fail):
                with TestClient(create_app(internal_api_token='token', review_service=reporter), raise_server_exceptions=False) as client:
                    response = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'assign', 'productId': self.product_id})
                    self.assertEqual(response.status_code, 500)
            self.pc.execute('SELECT product_id FROM document WHERE id=%s', (saved['documentId'],))
            self.assertIsNone(self.pc.fetchone()['product_id'])
            self.cc.execute('SELECT product_ids FROM document_snapshot WHERE id=%s', (saved['sourceId'],))
            self.assertEqual(json.loads(self.cc.fetchone()['product_ids']), [])
            self.cc.execute('SELECT COUNT(*) AS n FROM product_document_status WHERE product_id=%s', (self.product_id,))
            self.assertEqual(self.cc.fetchone()['n'], 0)
            self.cc.execute('SELECT status FROM review_item WHERE id=%s', (item['id'],))
            self.assertEqual(self.cc.fetchone()['status'], 'pending')
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                self.assertEqual(client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'assign', 'productId': self.product_id}).status_code, 200)
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))

    async def test_concurrent_reviews_share_file_and_preserve_both_snapshot_products(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        import json
        second_name = self.name + '-concurrent'
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, second_name, second_name))
        second_id = self.pc.lastrowid
        candidate = {**self.candidate, 'productIds': []}
        saved = await self.service.ingest_candidate(self.company_id, candidate, task_id=self.task_id)
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        payload = {'kind': 'document_attribution', 'candidate': candidate, 'documentId': saved['documentId'], 'sourceId': saved['sourceId']}
        first = reporter._record(self.task_id, 'document_attribution', payload)
        second = reporter._record(self.task_id, 'document_attribution', payload)
        try:
            results = await asyncio.gather(asyncio.to_thread(reporter.resolve, first['id'], {'action': 'assign', 'productId': self.product_id}), asyncio.to_thread(reporter.resolve, second['id'], {'action': 'assign', 'productId': second_id}))
            self.assertTrue(all(result['item']['status'] == 'resolved' for result in results))
            self.pc.execute('SELECT product_id,storage_key FROM document WHERE product_id IN (%s,%s)', (self.product_id, second_id))
            docs = self.pc.fetchall()
            self.assertEqual({row['product_id'] for row in docs}, {self.product_id, second_id})
            self.assertEqual(len({row['storage_key'] for row in docs}), 1)
            self.cc.execute('SELECT product_ids FROM document_snapshot WHERE id=%s', (saved['sourceId'],))
            self.assertEqual(set(json.loads(self.cc.fetchone()['product_ids'])), {self.product_id, second_id})
            self.cc.execute("SELECT COUNT(*) AS n FROM product_document_status WHERE product_id IN (%s,%s) AND status='downloaded'", (self.product_id, second_id))
            self.assertEqual(self.cc.fetchone()['n'], 2)
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))
            self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
            self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM document WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM product WHERE id=%s AND company_id=%s', (second_id, self.company_id))

    async def test_repeated_file_reuses_document_and_preserves_each_snapshot(self):
        first = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        second = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        self.assertEqual((first['status'], second['status']), ('stored', 'stored'))
        self.assertEqual(first['documentId'], second['documentId'])
        self.assertNotEqual(first['sourceId'], second['sourceId'])
        self.assertEqual(len(self.storage.objects), 2)
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['materials'][0]['status'], 'downloaded')
        self.assertEqual(detail['sources'][0]['documentId'], first['documentId'])
        self.assertEqual(detail['sources'][0]['sourceUrl'], 'https://example.test/product/1')
        self.cc.execute('SELECT COUNT(*) AS n FROM document_snapshot WHERE task_id=%s', (self.task_id,))
        self.assertEqual(self.cc.fetchone()['n'], 2)

    async def test_retry_network_then_record_permanent_failure(self):
        calls = []
        async def failing(url, **kwargs):
            calls.append(url)
            return {'ok': False, 'reason': 'network', 'message': '连接失败'}
        self.service.download = failing
        result = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id, retry_attempts=3)
        self.assertEqual((result['status'], result['reason']), ('failed', 'network'))
        self.assertEqual(len(calls), 3)
        self.cc.execute('SELECT reason, message, type, product_id FROM document_failure WHERE task_id=%s', (self.task_id,))
        self.assertEqual(self.cc.fetchone(), {'reason': 'network', 'message': '连接失败', 'type': 'clause', 'product_id': self.product_id})
        self.cc.execute('SELECT status,reason FROM product_document_status WHERE product_id=%s AND type=%s', (self.product_id, 'clause'))
        self.assertEqual(self.cc.fetchone(), {'status': 'download_failed', 'reason': 'network: 连接失败'})
        self.assertEqual(self.storage.objects, {})

    async def test_format_failure_is_not_retried(self):
        calls = []
        async def failing(url, **kwargs):
            calls.append(url)
            return {'ok': False, 'reason': 'format', 'message': '不是 PDF'}
        self.service.download = failing
        result = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id, retry_attempts=3)
        self.assertEqual(result['reason'], 'format')
        self.assertEqual(len(calls), 1)

    async def test_changed_pdf_preserves_old_version_and_explicit_history(self):
        first = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        async def changed(url, **kwargs): return {'ok': True, 'body': b'%PDF-version-two', 'contentType': 'application/pdf', 'finalUrl': url}
        self.service.download = changed
        second = await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        self.assertNotEqual(first['documentId'], second['documentId'])
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual([source['documentId'] for source in detail['sources']], [second['documentId']])
        self.assertEqual([source['documentId'] for source in detail['history']], [first['documentId']])
        historical = {**self.candidate, 'versionStatus': 'historical'}
        third = await self.service.ingest_candidate(self.company_id, historical, product_id=self.product_id, task_id=self.task_id)
        self.assertEqual(third['documentId'], second['documentId'])
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['sources'], [])
        self.assertEqual({source['documentId'] for source in detail['history']}, {first['documentId'], second['documentId']})

    async def test_shared_document_reuses_object_and_links_each_product(self):
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, self.name + '-2', self.name + '-2'))
        second_id = self.pc.lastrowid
        candidate = {**self.candidate, 'productIds': [self.product_id, second_id], 'shared': True}
        try:
            stored = await self.service.ingest_candidate(self.company_id, candidate, task_id=self.task_id)
            for product_id in candidate['productIds']:
                await self.service.link_to_product(product_id, stored['documentId'], stored['sourceId'], candidate, task_id=self.task_id)
            self.pc.execute('SELECT product_id,storage_key FROM document WHERE product_id IN (%s,%s) ORDER BY product_id', (self.product_id, second_id))
            records = self.pc.fetchall()
            self.assertEqual([item['product_id'] for item in records], [self.product_id, second_id])
            self.assertEqual(len({item['storage_key'] for item in records}), 1)
            for product_id in candidate['productIds']:
                detail = MySQLProductService(test_database_url(), test_crawl_url()).get(product_id)
                self.assertEqual(detail['materials'][0]['status'], 'downloaded')
                self.assertTrue(detail['sources'][0]['snapshotKey'])
        finally:
            self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM document WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM product WHERE id=%s', (second_id,))

    async def test_downloaded_status_is_not_overwritten_by_later_missing_or_failure(self):
        await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        await self.service.mark_not_found(self.product_id, 'clause', self.task_id + 1)
        await self.service.mark_download_failed(self.product_id, 'clause', self.task_id + 1, '读取失败')
        await self.service.mark_attribution_pending(self.product_id, 'clause', self.task_id + 1)
        self.cc.execute('SELECT status,task_id FROM product_document_status WHERE product_id=%s AND type=%s', (self.product_id, 'clause'))
        self.assertEqual(self.cc.fetchone(), {'status': 'downloaded', 'task_id': self.task_id})

    async def test_product_integration_is_partial_until_four_current_types_exist(self):
        await self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id)
        await self.service.set_product_integration_status(self.product_id, 'completed', self.task_id)
        self.pc.execute('SELECT integration_status,integration_status_reason FROM product WHERE id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone(), {'integration_status': 'partial_failed', 'integration_status_reason': '四类资料尚未齐全'})

    async def test_concurrent_same_document_resolves_unique_constraint_race(self):
        class BarrierStorage(MemoryStorage):
            def __init__(self):
                super().__init__()
                self.count = 0
                self.ready = asyncio.Event()
            async def put(self, key, body, content_type):
                if key.startswith('documents/'):
                    self.count += 1
                    if self.count == 2: self.ready.set()
                    await asyncio.wait_for(self.ready.wait(), timeout=4)
                await super().put(key, body, content_type)
        self.storage = BarrierStorage()
        self.service.storage = self.storage
        results = await asyncio.gather(*(self.service.ingest_candidate(self.company_id, self.candidate, product_id=self.product_id, task_id=self.task_id) for _ in range(2)), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException): raise result
        self.assertEqual(len({result['documentId'] for result in results}), 1)
        self.assertNotEqual(results[0]['sourceId'], results[1]['sourceId'])
        self.pc.execute('SELECT COUNT(*) AS n FROM document WHERE product_id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone()['n'], 1)
