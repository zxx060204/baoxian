import asyncio
import json
import unittest
from uuid import uuid4
from unittest.mock import patch

import httpx
import redis.asyncio as redis
from bullmq import Queue
from baoxian_api.worker import create_worker
from baoxian_api.worker_runtime import create_runtime
from baoxian_api.rate_limit import RedisCompanyLock
from test_queue import test_redis_url
from baoxian_api.company_adapter import CompanyAdapter
from baoxian_api.company_registry import CompanyRegistry
from baoxian_api.company_gateway import CompanyHttpGateway

from test_company_api import test_database_url
from test_product_api import test_crawl_url
from test_document_ingestion import MemoryStorage
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.document_ingestion import DocumentIngestionService
from baoxian_api.products import MySQLProductService
from baoxian_api.tasks import MySQLTaskService
from baoxian_api.review_reporter import MySQLReviewReporter
from baoxian_api.company_worker import CompanyDocumentProcessor


class CompanyWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product = MySQLCompanyService(test_database_url())._connect(); self.pc = self.product.cursor()
        self.crawl = MySQLCompanyService(test_crawl_url())._connect(); self.cc = self.crawl.cursor()
        self.name = 'python-company-worker-' + uuid4().hex
        self.pc.execute('INSERT INTO company (name,official_site_url) VALUES (%s,%s)', (self.name, 'https://example.test'))
        self.company_id = self.pc.lastrowid
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, self.name, self.name))
        self.product_id = self.pc.lastrowid
        self.scope = {'category': {'code': f'company:{self.company_id}', 'name': self.name}, 'company': {'id': self.company_id, 'name': self.name, 'officialSiteUrl': 'https://example.test'}, 'products': [{'id': self.product_id, 'name': self.name, 'salesStatus': 'on_sale'}]}
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents','queued',%s,%s)", (json.dumps(self.scope), json.dumps({'retryAttempts': 1, 'domainIntervalMs': 100, 'requestTimeoutMs': 3000})))
        self.task_id = self.cc.lastrowid
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (self.task_id, f'company:{self.company_id}:product:{self.product_id}'))
        self.tasks = MySQLTaskService(test_crawl_url())
        self.review = MySQLReviewReporter(test_crawl_url())
        async def download(url, **kwargs): return {'ok': True, 'body': b'%PDF-example', 'contentType': 'application/pdf', 'finalUrl': url}
        self.ingestion = DocumentIngestionService(test_database_url(), test_crawl_url(), MemoryStorage(), download)
        self.candidate = {'productIds': [self.product_id], 'type': 'clause', 'title': '条款', 'downloadUrl': 'https://example.test/' + self.name + '.pdf', 'sourcePageUrl': 'https://example.test/list', 'snapshotHtml': '<html>产品资料</html>'}

    async def asyncTearDown(self):
        self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM document_failure WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (self.product_id,))
        self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (self.task_id,))
        self.pc.execute('DELETE FROM document WHERE product_id=%s OR (product_id IS NULL AND download_url=%s)', (self.product_id, self.candidate['downloadUrl']))
        self.pc.execute('DELETE FROM product WHERE id=%s', (self.product_id,))
        self.pc.execute('DELETE FROM company WHERE id=%s AND name=%s', (self.company_id, self.name))
        self.cc.close(); self.crawl.close(); self.pc.close(); self.product.close()

    async def test_api_to_worker_company_document_flow_for_aia_and_chinalife(self):
        from baoxian_api.app import create_app
        from baoxian_api.queue import BullMQTaskQueue
        from baoxian_api.settings import MySQLSettingsService
        cases = [
            ('https://www.aia.com.cn', {'success': True, 'product_publish': {'totalPage': 1, 'records': [{'productName': self.name, 'productItem': 'terms/example'}]}}, 'cws.aia.com.cn'),
            ('https://www.e-chinalife.com', {'count': 1, 'list': [{'productname': self.name, 'clause': '/example.pdf'}]}, 'www.e-chinalife.com'),
        ]
        for site, payload, expected_host in cases:
            with self.subTest(site=site):
                self.pc.execute('UPDATE company SET official_site_url=%s WHERE id=%s', (site, self.company_id))
                queue_name = 'python-tdd-' + uuid4().hex
                queue = BullMQTaskQueue(test_redis_url(), queue_name)
                settings = MySQLSettingsService(test_crawl_url())
                tasks = MySQLTaskService(test_crawl_url(), settings=settings, queue=queue)
                products = MySQLProductService(test_database_url(), test_crawl_url())
                app = create_app(internal_api_token='token', company_service=MySQLCompanyService(test_database_url()), product_service=products, task_service=tasks)
                hosts = []
                def respond(request):
                    hosts.append(request.url.host)
                    return httpx.Response(200, json=payload)
                created_id = None
                try:
                    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source_client:
                        runtime = await create_runtime({'DATABASE_URL': test_database_url(), 'CRAWL_DATABASE_URL': test_crawl_url(), 'REDIS_URL': test_redis_url(), 'CRAWL_QUEUE_NAME': queue_name}, client=source_client, storage=MemoryStorage(), download=self.ingestion.download, enable_refresh=False, enable_recovery=False)
                        try:
                            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
                                created = await api.post('/api/crawl-tasks', headers={'Authorization': 'Bearer token'}, json={'type': 'company_documents', 'companyId': self.company_id, 'productIds': [self.product_id]})
                                self.assertEqual(created.status_code, 202)
                                created_id = created.json()['id']
                                async def wait_done():
                                    while True:
                                        response = await api.get(f'/api/crawl-tasks/{created_id}', headers={'Authorization': 'Bearer token'})
                                        task = response.json()
                                        if task['status'] in ('completed', 'failed', 'partial_failed', 'cancelled'): return task
                                        await asyncio.sleep(.05)
                                task = await asyncio.wait_for(wait_done(), 12)
                                self.assertEqual((task['status'], task['readCount']), ('completed', 1))
                                self.assertEqual(hosts, [expected_host])
                                self.cc.execute('SELECT COUNT(*) AS n FROM document_snapshot WHERE task_id=%s', (created_id,))
                                self.assertEqual(self.cc.fetchone()['n'], 1)
                                self.pc.execute('SELECT COUNT(*) AS n FROM document WHERE product_id=%s', (self.product_id,))
                                self.assertEqual(self.pc.fetchone()['n'], 1)
                        finally:
                            await runtime.close()
                finally:
                    await queue.queue.obliterate(force=True)
                    await queue.close()
                    if created_id:
                        self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (created_id,))
                        self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (created_id,))
                        self.cc.execute('DELETE FROM document_failure WHERE task_id=%s', (created_id,))
                        self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (created_id,))
                        self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (created_id,))
                    self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (self.product_id,))
                    self.pc.execute('DELETE FROM document WHERE product_id=%s', (self.product_id,))

    async def test_later_page_failure_keeps_first_page_document_for_both_adapters(self):
        from baoxian_api.aia_adapter import AiaAdapter
        from baoxian_api.chinalife_adapter import ChinaLifeAdapter
        for kind in ('aia', 'chinalife'):
            with self.subTest(kind=kind):
                name = self.name
                class Gateway:
                    def __init__(self): self.calls = 0
                    async def fetch_page(self, url, request=None):
                        self.calls += 1
                        if self.calls == 2: raise RuntimeError('第二页不可用')
                        if kind == 'aia':
                            payload = {'success': True, 'product_publish': {'totalPage': 2, 'records': [{'productName': name, 'productItem': 'terms/test'}]}}
                        else:
                            payload = {'count': 16, 'list': [{'productname': name, 'clause': '/terms/test.pdf'}]}
                        return {'url': url, 'html': json.dumps(payload, ensure_ascii=False)}
                gateway = Gateway()
                adapter = AiaAdapter(gateway) if kind == 'aia' else ChinaLifeAdapter(gateway)
                await CompanyDocumentProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(self.task_id)
                task = self.tasks.get(self.task_id)
                self.assertEqual((task['status'], task['readCount'], task['units'][0]['state']), ('partial_failed', 1, 'failed'))
                self.assertIn('第二页不可用', task['units'][0]['lastError'])
                self.pc.execute('SELECT COUNT(*) AS n FROM document WHERE product_id=%s', (self.product_id,))
                self.assertEqual(self.pc.fetchone()['n'], 1)
                self.cc.execute('SELECT type,status FROM product_document_status WHERE product_id=%s', (self.product_id,))
                self.assertEqual([(row['type'], row['status']) for row in self.cc.fetchall()], [('clause', 'downloaded')])
                self.cc.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
                self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (self.product_id,))
                self.pc.execute('DELETE FROM document WHERE product_id=%s', (self.product_id,))
                self.cc.execute("UPDATE crawl_task SET status='queued',scope=%s WHERE id=%s", (json.dumps(self.scope), self.task_id))
                self.cc.execute("UPDATE crawl_task_unit SET state='pending',last_error=NULL WHERE task_id=%s", (self.task_id,))

    async def test_cancel_during_startup_does_not_revive_task(self):
        tasks, ingestion = self.tasks, self.ingestion
        self.pc.execute("UPDATE product SET integration_status='failed',integration_status_reason='旧失败',integration_last_task_id=987 WHERE id=%s", (self.product_id,))
        class Ingestion:
            async def begin_product_integration(self, product_id, task_id):
                previous = await ingestion.begin_product_integration(product_id, task_id)
                tasks.cancel(task_id)
                return previous
            async def restore_product_integration(self, *args):
                await ingestion.restore_product_integration(*args)
        class Adapter:
            async def discover_documents(self, company, products):
                raise AssertionError('已取消任务不应请求官网')
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), Ingestion(), self.review).process(self.task_id)
        self.assertEqual(self.tasks.get(self.task_id)['status'], 'cancelled')
        self.pc.execute('SELECT integration_status,integration_status_reason,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone(), {'integration_status': 'failed', 'integration_status_reason': '旧失败', 'integration_last_task_id': 987})

    async def test_discovered_pdf_is_saved_and_missing_types_are_recorded(self):
        candidate = self.candidate
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': ['https://example.test/list'], 'pageFailures': []}
        processor = CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        await processor.process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount']), ('completed', 1, 1))
        self.assertEqual(task['units'][0]['state'], 'completed')
        self.assertEqual(task['scope']['searchedPages'], ['https://example.test/list'])
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['integrationStatus'], 'partial_failed')
        self.assertEqual([material['status'] for material in detail['materials']], ['downloaded','not_found','not_found','not_found'])
        self.cc.execute("SELECT COUNT(*) AS n FROM review_item WHERE task_id=%s AND type='document_attribution'", (self.task_id,))
        self.assertEqual(self.cc.fetchone()['n'], 0)

    async def test_first_directory_page_failure_is_reported_on_product_and_task(self):
        class Adapter:
            async def discover_documents(self, company, products): raise RuntimeError('目录超时')
        with self.assertRaisesRegex(RuntimeError, '目录超时'):
            await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['units'][0]['state']), ('failed', 'failed'))
        self.assertIn('目录超时', task['units'][0]['lastError'])
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual((detail['integrationStatus'], detail['integrationStatusReason']), ('failed', '目录超时'))

    async def test_historical_disclosure_pdf_is_not_linked_as_current(self):
        candidate = {**self.candidate, 'versionStatus': 'historical'}
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': [candidate['sourcePageUrl']], 'pageFailures': []}
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        self.pc.execute('SELECT type,link_status FROM document WHERE product_id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchall(), [{'type': 'clause', 'link_status': 'historical'}])
        self.cc.execute("SELECT status FROM product_document_status WHERE product_id=%s AND type='clause'", (self.product_id,))
        self.assertEqual(self.cc.fetchone()['status'], 'not_found')

    async def test_unassigned_pdf_creates_review_without_guessing_owner(self):
        candidate = {**self.candidate, 'productIds': []}
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': [candidate['sourcePageUrl']], 'pageFailures': []}
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        self.cc.execute("SELECT payload FROM review_item WHERE task_id=%s AND type='document_attribution'", (self.task_id,))
        row = self.cc.fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row['payload'])
        self.assertEqual(payload['candidate']['productIds'], [])
        self.pc.execute('SELECT product_id FROM document WHERE download_url=%s', (candidate['downloadUrl'],))
        self.assertIsNone(self.pc.fetchone()['product_id'])

    async def test_page_failure_does_not_mark_unsearched_materials_not_found(self):
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [], 'searchedPages': ['https://example.test/'], 'pageFailures': [{'url': 'https://example.test/page2', 'message': '读取失败'}]}
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['units'][0]['state']), ('partial_failed', 'failed'))
        self.assertIn('page2', task['units'][0]['lastError'])
        self.cc.execute('SELECT COUNT(*) AS n FROM product_document_status WHERE product_id=%s', (self.product_id,))
        self.assertEqual(self.cc.fetchone()['n'], 0)
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['integrationStatus'], 'partial_failed')

    async def test_complete_empty_search_replaces_prior_download_failure_with_not_found(self):
        self.cc.execute("INSERT INTO product_document_status (product_id,type,status,reason,task_id) VALUES (%s,'clause','download_failed','旧任务下载失败',%s)", (self.product_id, self.task_id + 900000))
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [], 'searchedPages': ['https://example.test/list'], 'pageFailures': []}
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        self.cc.execute("SELECT status,reason,task_id FROM product_document_status WHERE product_id=%s AND type='clause'", (self.product_id,))
        self.assertEqual(self.cc.fetchone(), {'status': 'not_found', 'reason': None, 'task_id': self.task_id})
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(next(item['status'] for item in detail['materials'] if item['type'] == 'clause'), 'not_found')
        self.assertEqual(self.tasks.get(self.task_id)['status'], 'completed')

    async def test_cancel_during_postprocessing_does_not_complete_task_unit(self):
        tasks, ingestion = self.tasks, self.ingestion
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [], 'searchedPages': ['https://example.test/list'], 'pageFailures': []}
        class CancelAfterNotFound:
            def __init__(self): self.cancelled = False
            def __getattr__(self, name): return getattr(ingestion, name)
            async def mark_not_found(self, product_id, kind, task_id=None):
                await ingestion.mark_not_found(product_id, kind, task_id)
                if not self.cancelled:
                    self.cancelled = True
                    tasks.cancel(task_id)
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), CancelAfterNotFound(), self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'cancelled')
        self.assertTrue(all(unit['state'] != 'completed' for unit in task['units']))

    async def test_cancellation_during_discovery_stops_before_download(self):
        started = asyncio.Event()
        class Adapter:
            async def discover_documents(self, company, products):
                started.set()
                await asyncio.Event().wait()
        worker = CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        running = asyncio.create_task(worker.process(self.task_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            self.tasks.cancel(self.task_id)
            await asyncio.wait_for(running, timeout=3)
            self.assertEqual(self.tasks.get(self.task_id)['status'], 'cancelled')
            self.pc.execute('SELECT COUNT(*) AS n FROM document WHERE product_id=%s', (self.product_id,))
            self.assertEqual(self.pc.fetchone()['n'], 0)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_cancel_after_worker_exit_restores_persisted_previous_status(self):
        self.pc.execute("UPDATE product SET integration_status='failed',integration_status_reason='旧失败',integration_last_task_id=987 WHERE id=%s", (self.product_id,))
        await self.ingestion.begin_product_integration(self.product_id, self.task_id)
        self.assertNotIn('_integrationBefore', self.tasks.get(self.task_id)['scope'])
        service = MySQLTaskService(test_crawl_url(), product_database_url=test_database_url())
        from baoxian_api.app import create_app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(internal_api_token='token', task_service=service)), base_url='http://test') as client:
            response = await client.post(f'/api/crawl-tasks/{self.task_id}/cancel', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        self.pc.execute('SELECT integration_status,integration_status_reason,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone(), {'integration_status': 'failed', 'integration_status_reason': '旧失败', 'integration_last_task_id': 987})
        self.assertEqual(service.get(self.task_id)['status'], 'cancelled')

    async def test_cancel_rollback_preserves_task_and_product_when_restore_fails(self):
        await self.ingestion.begin_product_integration(self.product_id, self.task_id)
        service = MySQLTaskService(test_crawl_url(), product_database_url=test_database_url())
        original_connect = service._connect
        class Cursor:
            def __init__(self, inner): self.inner = inner
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def __getattr__(self, name): return getattr(self.inner, name)
            def execute(self, sql, args=None):
                if sql.startswith('UPDATE `') and '.product SET integration_status=' in sql:
                    raise RuntimeError('restore failed')
                return self.inner.execute(sql, args)
        class Connection:
            def __init__(self, inner): self.inner = inner
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def __getattr__(self, name): return getattr(self.inner, name)
            def cursor(self): return Cursor(self.inner.cursor())
        with patch.object(service, '_connect', side_effect=lambda: Connection(original_connect())):
            with self.assertRaisesRegex(RuntimeError, 'restore failed'):
                service.cancel(self.task_id)
        self.assertEqual(service.get(self.task_id)['status'], 'queued')
        self.pc.execute('SELECT integration_status,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone(), {'integration_status': 'running', 'integration_last_task_id': self.task_id})

    async def test_cancel_after_worker_exit_preserves_other_task_status(self):
        await self.ingestion.begin_product_integration(self.product_id, self.task_id)
        self.pc.execute("UPDATE product SET integration_status='running',integration_last_task_id=999 WHERE id=%s", (self.product_id,))
        service = MySQLTaskService(test_crawl_url(), product_database_url=test_database_url())
        service.cancel(self.task_id)
        self.pc.execute('SELECT integration_status,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
        self.assertEqual(self.pc.fetchone(), {'integration_status': 'running', 'integration_last_task_id': 999})

    async def test_cancellation_restores_previous_product_status(self):
        self.pc.execute("UPDATE product SET integration_status='failed',integration_status_reason='旧失败',integration_last_task_id=987 WHERE id=%s", (self.product_id,))
        started = asyncio.Event()
        class Adapter:
            async def discover_documents(self, company, products):
                started.set()
                await asyncio.Event().wait()
        worker = CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        running = asyncio.create_task(worker.process(self.task_id))
        try:
            await asyncio.wait_for(started.wait(), 3)
            self.tasks.cancel(self.task_id)
            await asyncio.wait_for(running, 3)
            self.pc.execute('SELECT integration_status,integration_status_reason,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
            self.assertEqual(self.pc.fetchone(), {'integration_status': 'failed', 'integration_status_reason': '旧失败', 'integration_last_task_id': 987})
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_cancellation_does_not_overwrite_newer_product_status(self):
        started = asyncio.Event()
        class Adapter:
            async def discover_documents(self, company, products):
                started.set()
                await asyncio.Event().wait()
        worker = CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        running = asyncio.create_task(worker.process(self.task_id))
        try:
            await asyncio.wait_for(started.wait(), 3)
            self.pc.execute("UPDATE product SET integration_status='running',integration_last_task_id=999 WHERE id=%s", (self.product_id,))
            self.tasks.cancel(self.task_id)
            await asyncio.wait_for(running, 3)
            self.pc.execute('SELECT integration_status,integration_last_task_id FROM product WHERE id=%s', (self.product_id,))
            self.assertEqual(self.pc.fetchone(), {'integration_status': 'running', 'integration_last_task_id': 999})
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_http_adapter_processor_and_ingestion_work_together(self):
        html = f'<table><tr><td>{self.name}</td><td><a href="/{self.name}.pdf">条款</a></td></tr></table>'
        waits = []
        async def wait(host, interval): waits.append((host, interval))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))) as client:
            gateway = CompanyHttpGateway(client, lambda: {'retryAttempts': 1, 'domainIntervalMs': 100, 'requestTimeoutMs': 3000}, wait)
            adapter = CompanyAdapter('generic', gateway)
            await CompanyDocumentProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(self.task_id)
        self.assertEqual(waits, [('example.test', 100)])
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount']), ('completed', 1))
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['materials'][0]['status'], 'downloaded')
        self.assertEqual(detail['sources'][0]['downloadUrl'], self.candidate['downloadUrl'])

    async def test_download_failure_marks_product_and_task_failed(self):
        async def download(url, **kwargs): return {'ok': False, 'reason': 'http', 'message': 'HTTP 404'}
        self.ingestion.download = download
        candidate = self.candidate
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': [candidate['sourcePageUrl']], 'pageFailures': []}
        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['units'][0]['state']), ('failed', 'failed'))
        self.assertIn('HTTP 404', task['units'][0]['lastError'])
        self.cc.execute('SELECT status FROM product_document_status WHERE product_id=%s AND type=%s', (self.product_id, 'clause'))
        self.assertEqual(self.cc.fetchone()['status'], 'download_failed')
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.product_id)
        self.assertEqual(detail['integrationStatus'], 'failed')

    async def test_shared_candidate_links_multiple_products(self):
        second_name = self.name + '-2'
        self.pc.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试')", (self.company_id, second_name, second_name))
        second_id = self.pc.lastrowid
        self.scope['products'].append({'id': second_id, 'name': second_name})
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (self.task_id, f'company:{self.company_id}:product:{second_id}'))
        candidate = {**self.candidate, 'productIds': [self.product_id, second_id], 'shared': True}
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': [candidate['sourcePageUrl']], 'pageFailures': []}
        try:
            await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
            self.assertEqual(self.tasks.get(self.task_id)['status'], 'completed')
            self.pc.execute('SELECT product_id,storage_key FROM document WHERE product_id IN (%s,%s) ORDER BY product_id', (self.product_id, second_id))
            docs = self.pc.fetchall()
            self.assertEqual([row['product_id'] for row in docs], [self.product_id, second_id])
            self.assertEqual(len({row['storage_key'] for row in docs}), 1)
        finally:
            self.cc.execute('DELETE FROM product_document_status WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM document WHERE product_id=%s', (second_id,))
            self.pc.execute('DELETE FROM product WHERE id=%s', (second_id,))

    async def _check_registered_company_chain(self, site, payload, expected_host):
        self.scope['company']['officialSiteUrl'] = site
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        requested = []
        def respond(request):
            requested.append(request)
            return httpx.Response(200, json=payload)
        async def wait(host, interval): pass
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = CompanyHttpGateway(client, lambda: {'retryAttempts': 1, 'domainIntervalMs': 0, 'requestTimeoutMs': 3000}, wait)
            processor = CompanyDocumentProcessor(test_crawl_url(), CompanyRegistry(gateway), self.ingestion, self.review)
            queue_name = 'python-tdd-' + uuid4().hex
            queue = Queue(queue_name, {'connection': test_redis_url()})
            runtime = await create_runtime({'DATABASE_URL': test_database_url(), 'CRAWL_DATABASE_URL': test_crawl_url(), 'REDIS_URL': test_redis_url(), 'CRAWL_QUEUE_NAME': queue_name}, client=client, storage=MemoryStorage(), download=self.ingestion.download, enable_refresh=False)
            try:
                await queue.add('association_index', {'taskId': self.task_id})
                async def wait_until_done():
                    while True:
                        task = await asyncio.to_thread(self.tasks.get, self.task_id)
                        if task['status'] in ('completed', 'failed', 'partial_failed', 'cancelled'):
                            return
                        await asyncio.sleep(.05)
                await asyncio.wait_for(wait_until_done(), timeout=10)
            finally:
                await runtime.close()
                await queue.obliterate(force=True)
                await queue.close()
        self.assertEqual(requested[0].url.host, expected_host)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['units'][0]['state']), ('completed', 1, 'completed'))
        self.pc.execute('SELECT type FROM document WHERE product_id=%s', (self.product_id,))
        self.assertEqual([row['type'] for row in self.pc.fetchall()], ['clause'])
        self.cc.execute('SELECT snapshot_kind FROM document_snapshot WHERE task_id=%s', (self.task_id,))
        self.assertEqual(self.cc.fetchone()['snapshot_kind'], 'api_response')
        self.cc.execute("SELECT status FROM product_document_status WHERE product_id=%s AND type='clause'", (self.product_id,))
        self.assertEqual(self.cc.fetchone()['status'], 'downloaded')

    async def test_aia_registry_through_document_ingestion(self):
        payload = {'success': True, 'product_publish': {'totalPage': 1, 'records': [{'productName': self.name, 'productItem': 'terms/example'}]}}
        await self._check_registered_company_chain('https://www.aia.com.cn', payload, 'cws.aia.com.cn')

    async def test_chinalife_registry_through_document_ingestion(self):
        payload = {'count': 1, 'list': [{'productname': self.name, 'clause': '/example.pdf'}]}
        await self._check_registered_company_chain('https://www.e-chinalife.com', payload, 'www.e-chinalife.com')
