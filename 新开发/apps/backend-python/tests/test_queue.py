import asyncio
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from bullmq import Queue, Worker
from baoxian_api.queue import BullMQTaskQueue
from baoxian_api.tasks import MySQLTaskService
from test_product_api import test_crawl_url


def test_redis_url():
    value = os.environ.get('TEST_REDIS_URL')
    if not value:
        for line in (Path(__file__).resolve().parents[3] / '.env').read_text(encoding='utf-8').splitlines():
            key, sep, item = line.partition('=')
            if sep and key.strip() == 'TEST_REDIS_URL':
                value = item.strip().strip('\"\'')
                break
    if not value or urlparse(value).path != '/1':
        raise RuntimeError('队列测试只允许使用 Redis 测试库 1')
    return value


class QueueCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = test_redis_url()
        self.name = 'python-tdd-' + uuid4().hex
        self.queue = BullMQTaskQueue(self.url, self.name)

    async def asyncTearDown(self):
        if not self.name.startswith('python-tdd-'):
            raise RuntimeError('拒绝清理非测试队列')
        try:
            await self.queue.queue.obliterate(force=True)
        finally:
            await self.queue.close()

    async def test_python_enqueue_is_readable_by_node_bullmq(self):
        await self.queue.enqueue(147)
        code = '''const { Queue } = require('bullmq'); const { Redis } = require('ioredis'); (async () => { const connection = new Redis(process.env.BAOXIAN_TEST_REDIS_URL, {maxRetriesPerRequest:null}); const q = new Queue(process.argv[1], {connection}); const job = await q.getJob('crawl-147'); console.log(JSON.stringify({name:job.name, data:job.data, id:job.id, opts:job.opts})); await job.remove(); await q.close(); connection.disconnect(); })().catch(e => { console.error(e.message); process.exit(1); });'''
        env = dict(os.environ, BAOXIAN_TEST_REDIS_URL=self.url)
        proc = await asyncio.create_subprocess_exec('node', '-e', code, self.name, cwd=str(Path(__file__).resolve().parents[3]), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        job = json.loads(stdout)
        self.assertEqual((job['name'], job['id'], job['data']), ('association_index', 'crawl-147', {'taskId': 147}))
        self.assertEqual(job['opts']['removeOnComplete'], 1000)
        self.assertEqual(job['opts']['removeOnFail'], 1000)

    async def test_upsert_schedule_is_visible_to_node_and_removable(self):
        await self.queue.upsert_schedule(17, 'PubNewProdTypeCode_00', 3)
        code = '''const { Queue } = require('bullmq'); const { Redis } = require('ioredis'); (async () => { const c = new Redis(process.env.BAOXIAN_TEST_REDIS_URL, {maxRetriesPerRequest:null}); const q = new Queue(process.argv[1], {connection:c}); const jobs = await q.getJobSchedulers(); console.log(JSON.stringify(jobs)); await q.close(); c.disconnect(); })().catch(e => {console.error(e.message);process.exit(1)});'''
        env = dict(os.environ, BAOXIAN_TEST_REDIS_URL=self.url)
        proc = await asyncio.create_subprocess_exec('node', '-e', code, self.name, cwd=str(Path(__file__).resolve().parents[3]), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        self.assertEqual(proc.returncode, 0, stderr.decode())
        jobs = json.loads(stdout)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['key'], 'schedule-17')
        self.assertEqual(jobs[0]['every'], 180000)
        await self.queue.remove_schedule(17)
        self.assertEqual(await self.queue.queue.getJobSchedulers(), [])

    async def test_schedule_service_registers_and_removes_real_scheduler(self):
        from baoxian_api.schedules import MySQLScheduleService
        from test_product_api import test_crawl_url
        service = MySQLScheduleService(test_crawl_url(), self.queue)
        code = 'test-' + uuid4().hex
        schedule = await service.create({'code': code, 'name': '测试'}, 4, True)
        try:
            schedulers = await self.queue.queue.getJobSchedulers()
            self.assertEqual((schedulers[0]['key'], schedulers[0]['every']), (f"schedule-{schedule['id']}", 240000))
            await service.update(schedule['id'], {'enabled': False})
            self.assertEqual(await self.queue.queue.getJobSchedulers(), [])
        finally:
            with service._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM crawl_schedule WHERE id=%s AND category_code=%s', (schedule['id'], code))

    async def test_runtime_registers_automatic_refresh_scheduler(self):
        from unittest.mock import patch, AsyncMock
        from baoxian_api.worker_runtime import create_runtime
        from test_company_api import test_database_url
        from test_product_api import test_crawl_url
        env = {'DATABASE_URL': test_database_url(), 'CRAWL_DATABASE_URL': test_crawl_url(), 'REDIS_URL': self.url, 'CRAWL_QUEUE_NAME': self.name}
        with patch('baoxian_api.index_refresh.IndexRefreshScheduler.check', new_callable=AsyncMock, return_value={'created': []}):
            runtime = await create_runtime(env)
            try:
                schedulers = await self.queue.queue.getJobSchedulers()
                self.assertEqual({item['key']: item['every'] for item in schedulers}, {
                    'association-refresh-check': 60000,
                    'task-state-recovery': 60000,
                })
            finally:
                await runtime.close()

    async def test_python_worker_advances_repeating_scheduler(self):
        observed = []
        enough = asyncio.Event()
        async def process(job, token):
            observed.append((job.name, job.data))
            if len(observed) >= 2:
                enough.set()
        worker = Worker(self.name, process, {'connection': self.url})
        try:
            await self.queue.queue.upsertJobScheduler('scheduler-repeat-test', {'every': 300}, job_name='scheduled_index', job_data={'scheduleId': 17, 'categoryCode': 'PubNewProdTypeCode_00'})
            await asyncio.wait_for(enough.wait(), timeout=8)
            self.assertEqual(observed[:2], [('scheduled_index', {'scheduleId': 17, 'categoryCode': 'PubNewProdTypeCode_00'})] * 2)
        finally:
            await worker.close()

    async def test_enqueue_confirmation_loss_checks_existing_job(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        real_queue = self.queue
        class LostAcknowledgement:
            task_id = None
            async def enqueue(self, task_id):
                self.task_id = task_id
                await real_queue.enqueue(task_id)
                raise TimeoutError('确认丢失')
            async def contains_task(self, task_id):
                return await real_queue.contains_task(task_id)
        queue = LostAcknowledgement()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            task = await service.create_index_task({'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'})
            self.assertEqual(task['status'], 'queued')
            self.assertEqual((await self.queue.queue.getJob(f"crawl-{task['id']}")).data, {'taskId': task['id']})
        finally:
            if queue.task_id:
                with service._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (queue.task_id,))
                        cursor.execute('DELETE FROM crawl_task WHERE id=%s', (queue.task_id,))

    async def test_enqueue_keeps_existing_nonterminal_job_idempotently(self):
        await self.queue.queue.add('association_index', {'taskId': 147, 'marker': 'original'}, {'jobId': 'crawl-147'})
        before = await self.queue.queue.getJob('crawl-147')

        await self.queue.enqueue(147)

        after = await self.queue.queue.getJob('crawl-147')
        self.assertEqual(after.data, {'taskId': 147, 'marker': 'original'})
        self.assertEqual(after.timestamp, before.timestamp)

    async def test_unconfirmed_enqueue_is_recovered_into_same_real_queue(self):
        from baoxian_api.tasks import MySQLTaskService, QueueUnavailableError

        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class UnavailableQueue:
            task_id = None
            async def enqueue(self, task_id):
                self.task_id = task_id
                raise TimeoutError('写入确认丢失')
            async def contains_task(self, task_id): raise TimeoutError('查询暂不可用')
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=UnavailableQueue())
        task_id = None
        try:
            with self.assertRaises(QueueUnavailableError):
                await service.create_index_task({'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'})
            task_id = service.queue.task_id

            service.queue = self.queue
            await service.reconcile_worker_state()

            job = await self.queue.queue.getJob(f'crawl-{task_id}')
            self.assertIsNotNone(job)
            self.assertEqual(job.data, {'taskId': task_id})
            self.assertEqual(service.get(task_id)['status'], 'queued')
        finally:
            if task_id:
                with service._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (task_id,))
                        cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task_id,))

    async def test_index_task_persists_and_enqueues_in_isolated_redis(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=self.queue)
        task = None
        try:
            task = await service.create_index_task({'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'})
            job = await self.queue.queue.getJob(f"crawl-{task['id']}")
            self.assertIsNotNone(job)
            self.assertEqual(job.data, {'taskId': task['id']})
            self.assertEqual(task['status'], 'queued')
            self.assertEqual(service.get(task['id'])['units'][0]['state'], 'pending')
        finally:
            if task:
                with service._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (task['id'],))
                        cursor.execute('DELETE FROM crawl_task WHERE id = %s', (task['id'],))
