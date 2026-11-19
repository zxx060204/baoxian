import asyncio
import unittest
from uuid import uuid4

from baoxian_api.index_refresh import IndexRefreshScheduler
from baoxian_api.tasks import MySQLTaskService
from test_product_api import test_crawl_url


class IndexRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.code = 'test-' + uuid4().hex
        self.connection = MySQLTaskService(test_crawl_url())._connect()
        self.cursor = self.connection.cursor()
        self.cursor.execute("INSERT INTO association_index_progress (category_code,category_name,completed_at) VALUES (%s,'测试',UTC_TIMESTAMP() - INTERVAL 120 MINUTE)", (self.code,))
        class Settings:
            enabled = True
            def get(inner): return {'associationCrawlEnabled': inner.enabled, 'indexRefreshMinutes': 60, 'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3}
        class Queue:
            def __init__(inner): inner.ids = []
            async def enqueue(inner, task_id): inner.ids.append(task_id)
        self.settings, self.queue = Settings(), Queue()
        self.tasks = MySQLTaskService(test_crawl_url(), settings=self.settings, queue=self.queue)
        self.scheduler = IndexRefreshScheduler(test_crawl_url(), self.settings, self.tasks, categories={self.code: '测试'})

    async def asyncTearDown(self):
        for task_id in self.queue.ids:
            self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (task_id,))
            self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task_id,))
        self.cursor.execute('DELETE FROM association_index_progress WHERE category_code=%s', (self.code,))
        self.cursor.close()
        self.connection.close()

    async def test_concurrent_checks_create_only_one_active_task(self):
        results = await asyncio.gather(self.scheduler.check(), self.scheduler.check())
        self.assertEqual(sum(len(result['created']) for result in results), 1)
        self.assertEqual(len(self.queue.ids), 1)
        task = self.tasks.get(self.queue.ids[0])
        self.assertEqual((task['scope']['category']['code'], task['status']), (self.code, 'queued'))
        self.assertEqual((await self.scheduler.check())['created'], [])

    async def test_disabled_and_not_due_do_not_create(self):
        self.settings.enabled = False
        self.assertEqual((await self.scheduler.check())['created'], [])
        self.settings.enabled = True
        self.cursor.execute('UPDATE association_index_progress SET completed_at=UTC_TIMESTAMP() WHERE category_code=%s', (self.code,))
        self.assertEqual((await self.scheduler.check())['created'], [])
        self.assertEqual(self.queue.ids, [])
