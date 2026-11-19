import asyncio
import json
import unittest
from uuid import uuid4

from test_company_api import test_database_url
from test_product_api import test_crawl_url
from test_queue import test_redis_url
from bullmq import Queue
from baoxian_api.worker import create_worker
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.review_reporter import MySQLReviewReporter
from baoxian_api.tasks import MySQLTaskService
from baoxian_api.site_worker import CompanySiteDiscoveryProcessor


class SiteWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product = MySQLCompanyService(test_database_url())._connect()
        self.pc = self.product.cursor()
        self.crawl = MySQLCompanyService(test_crawl_url())._connect()
        self.cc = self.crawl.cursor()
        self.name = 'python-site-' + uuid4().hex
        self.pc.execute('INSERT INTO company (name) VALUES (%s)', (self.name,))
        self.company_id = self.pc.lastrowid
        scope = {'category': {'code': 'company_site', 'name': '公司官网发现'}, 'companies': [{'id': self.company_id, 'name': self.name}]}
        self.cc.execute("INSERT INTO crawl_task (type,status,scope) VALUES ('company_site_discovery','queued',%s)", (json.dumps(scope, ensure_ascii=False),))
        self.task_id = self.cc.lastrowid
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (self.task_id, f'company-site:{self.company_id}'))
        self.tasks = MySQLTaskService(test_crawl_url())
        self.review = MySQLReviewReporter(test_crawl_url())

    async def asyncTearDown(self):
        self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (self.task_id,))
        self.pc.execute('DELETE FROM company WHERE id=%s AND name=%s', (self.company_id, self.name))
        self.cc.close(); self.crawl.close(); self.pc.close(); self.product.close()

    async def test_cancel_between_initial_read_and_start_does_not_revive(self):
        tasks = self.tasks
        class RacingTasks:
            def __init__(self): self.first = True
            def get(self, task_id):
                task = tasks.get(task_id)
                if self.first:
                    self.first = False
                    tasks.cancel(task_id)
                return task
        class Disclosure:
            async def find(self, name): raise AssertionError('已取消任务不应请求官网发现')
        processor = CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review)
        processor.tasks = RacingTasks()
        await processor.process(self.task_id)
        self.assertEqual(tasks.get(self.task_id)['status'], 'cancelled')

    async def test_found_site_is_written_and_task_completed(self):
        class Disclosure:
            async def find(self, name): return {'kind': 'found', 'url': 'https://example.com', 'sourceUrl': 'https://icidp.iachina.cn/detail'}
        await CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review).process(self.task_id)
        self.pc.execute('SELECT official_site_url FROM company WHERE id=%s', (self.company_id,))
        self.assertEqual(self.pc.fetchone()['official_site_url'], 'https://example.com')
        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(task['units'][0]['state'], 'completed')

    async def test_unmatched_records_review_and_fails_unit(self):
        class Disclosure:
            async def find(self, name): return {'kind': 'unavailable', 'reason': '协会目录未找到'}
        await CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['units'][0]['state'], task['units'][0]['lastError']), ('failed', 'failed', '协会目录未找到'))
        self.pc.execute('SELECT official_site_url FROM company WHERE id=%s', (self.company_id,))
        self.assertIsNone(self.pc.fetchone()['official_site_url'])
        self.cc.execute("SELECT payload FROM review_item WHERE task_id=%s AND type='company_site'", (self.task_id,))
        payload = json.loads(self.cc.fetchone()['payload'])
        self.assertEqual((payload['companyId'], payload['siteCandidates'], payload['reason']), (self.company_id, [], '协会目录未找到'))

    async def test_site_configured_during_lookup_is_not_overwritten(self):
        pc, company_id = self.pc, self.company_id
        class Disclosure:
            async def find(self, name):
                pc.execute('UPDATE company SET official_site_url=%s WHERE id=%s', ('https://existing.example', company_id))
                return {'kind': 'found', 'url': 'https://new.example', 'sourceUrl': 'https://icidp.iachina.cn/detail'}
        await CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review).process(self.task_id)
        self.pc.execute('SELECT official_site_url FROM company WHERE id=%s', (self.company_id,))
        self.assertEqual(self.pc.fetchone()['official_site_url'], 'https://existing.example')
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['units'][0]['state']), ('failed', 'failed'))

    async def test_cancellation_during_lookup_stops_without_writing_site(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        class Disclosure:
            async def find(self, name):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        worker = CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review)
        running = asyncio.create_task(worker.process(self.task_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            self.tasks.cancel(self.task_id)
            await asyncio.wait_for(running, timeout=3)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(self.tasks.get(self.task_id)['status'], 'cancelled')
            self.pc.execute('SELECT official_site_url FROM company WHERE id=%s', (self.company_id,))
            self.assertIsNone(self.pc.fetchone()['official_site_url'])
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_worker_dispatches_site_discovery_job(self):
        class Disclosure:
            async def find(self, name): return {'kind': 'found', 'url': 'https://example.com', 'sourceUrl': 'https://icidp.iachina.cn/detail'}
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        processor = CompanySiteDiscoveryProcessor(test_crawl_url(), test_database_url(), Disclosure(), self.review)
        worker = create_worker(queue_name, test_redis_url(), self.tasks, {'company_site_discovery': processor})
        try:
            await queue.add('association_index', {'taskId': self.task_id}, {'jobId': f'crawl-{self.task_id}'})
            async def wait_done():
                while True:
                    task = await asyncio.to_thread(self.tasks.get, self.task_id)
                    if task['status'] in ('completed', 'failed', 'partial_failed'):
                        return task
                    await asyncio.sleep(.05)
            task = await asyncio.wait_for(wait_done(), timeout=8)
            self.assertEqual(task['status'], 'completed')
            self.pc.execute('SELECT official_site_url FROM company WHERE id=%s', (self.company_id,))
            self.assertEqual(self.pc.fetchone()['official_site_url'], 'https://example.com')
        finally:
            await worker.close()
            if queue_name.startswith('python-tdd-'):
                await queue.obliterate(force=True)
            await queue.close()
