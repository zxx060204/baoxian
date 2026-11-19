import asyncio
import time
import unittest
from uuid import uuid4

import redis.asyncio as redis
from bullmq import Queue
from baoxian_api.worker import create_worker
from test_queue import test_redis_url
from baoxian_api.rate_limit import RedisDomainRateLimiter, RedisCompanyLock


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_workers_share_host_interval_and_cancellation(self):
        url = test_redis_url()
        client = redis.from_url(url)
        host = 'python-tdd-' + uuid4().hex + '.invalid'
        first = RedisDomainRateLimiter(client)
        second = RedisDomainRateLimiter(client)
        try:
            await first.wait(host, 400)
            start = time.monotonic()
            waiting = asyncio.create_task(second.wait(host, 400))
            await asyncio.sleep(.03)
            self.assertFalse(waiting.done())
            await asyncio.wait_for(waiting, timeout=3)
            self.assertGreaterEqual(time.monotonic() - start, .30)
            blocked = asyncio.create_task(first.wait(host, 400))
            await asyncio.sleep(.03)
            blocked.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await blocked
        finally:
            await client.delete('rate:domain:' + host)
            await client.aclose()

    async def test_company_lock_serializes_and_does_not_release_other_owner(self):
        client = redis.from_url(test_redis_url())
        company_id = int(uuid4().int % 1000000000000) + 1000000000000
        first = RedisCompanyLock(client, ttl_ms=600)
        second = RedisCompanyLock(client, ttl_ms=600)
        key = f'lock:company:{company_id}'
        release_first = release_second = None
        waiting = None
        try:
            release_first = await first.acquire(company_id)
            waiting = asyncio.create_task(second.acquire(company_id))
            await asyncio.sleep(.8)
            self.assertFalse(waiting.done(), '锁持有期间应续期')
            await release_first()
            release_first = None
            release_second = await asyncio.wait_for(waiting, timeout=3)
            await client.set(key, 'other-owner', px=3000)
            await release_second()
            release_second = None
            self.assertEqual(await client.get(key), b'other-owner')
        finally:
            if waiting and not waiting.done(): waiting.cancel(); await asyncio.gather(waiting, return_exceptions=True)
            if release_first: await release_first()
            if release_second: await release_second()
            await client.delete(key)
            await client.aclose()

    async def test_worker_serializes_company_document_jobs(self):
        client = redis.from_url(test_redis_url())
        company_id = int(uuid4().int % 1000000000000) + 1000000000000
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        class Tasks:
            def get(self, task_id): return {'type': 'company_documents', 'scope': {'company': {'id': company_id}}}
        class Processor:
            def __init__(self): self.active = 0; self.maximum = 0; self.done = 0; self.finished = asyncio.Event()
            async def process(self, task_id):
                self.active += 1; self.maximum = max(self.maximum, self.active)
                await asyncio.sleep(.2)
                self.active -= 1; self.done += 1
                if self.done == 2: self.finished.set()
        processor = Processor()
        worker = create_worker(queue_name, test_redis_url(), Tasks(), {'company_documents': processor}, concurrency=2, company_lock=RedisCompanyLock(client))
        try:
            await queue.add('association_index', {'taskId': 1})
            await queue.add('association_index', {'taskId': 2})
            await asyncio.wait_for(processor.finished.wait(), timeout=8)
            self.assertEqual(processor.maximum, 1)
        finally:
            await worker.close()
            if queue_name.startswith('python-tdd-'): await queue.obliterate(force=True)
            await queue.close()
            await client.delete(f'lock:company:{company_id}')
            await client.aclose()

    async def test_disabled_schedule_does_not_create_or_execute_task(self):
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        checked = asyncio.Event()
        class Schedules:
            async def run_if_enabled(self, schedule_id, code, operation):
                checked.set()
                return None
        class Tasks:
            def __init__(self): self.created = []
            async def create_index_task(self, *args, **kwargs): self.created.append(args); return {'id': 42}
        class Processor:
            async def process(self, task_id): raise AssertionError('停用计划不应执行')
        tasks = Tasks()
        worker = create_worker(queue_name, test_redis_url(), tasks, {'association_index': Processor()}, schedule_service=Schedules())
        try:
            await queue.add('scheduled_index', {'scheduleId': 3, 'categoryCode': 'PubNewProdTypeCode_01'})
            await asyncio.wait_for(checked.wait(), timeout=8)
            await asyncio.sleep(.2)
            self.assertEqual(tasks.created, [])
        finally:
            await worker.close()
            await queue.obliterate(force=True)
            await queue.close()

    async def test_scheduled_index_creates_task_without_requeue_and_processes(self):
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        class Tasks:
            def __init__(self): self.created = []
            async def create_index_task(self, category, *, enqueue=True, queue_job_id=None):
                self.created.append((category, enqueue, queue_job_id))
                return {'id': 42}
        class Processor:
            def __init__(self): self.done = asyncio.Event(); self.task_id = None
            async def process(self, task_id): self.task_id = task_id; self.done.set()
        class Schedules:
            async def run_if_enabled(self, schedule_id, code, operation):
                return await operation()
        tasks, processor = Tasks(), Processor()
        worker = create_worker(queue_name, test_redis_url(), tasks, {'association_index': processor}, schedule_service=Schedules())
        try:
            scheduled_job = await queue.add('scheduled_index', {'scheduleId': 3, 'categoryCode': 'PubNewProdTypeCode_01'})
            await asyncio.wait_for(processor.done.wait(), timeout=8)
            self.assertEqual(tasks.created, [({'code': 'PubNewProdTypeCode_01', 'name': '年金保险'}, False, str(scheduled_job.id))])
            self.assertEqual(processor.task_id, 42)
        finally:
            await worker.close()
            await queue.obliterate(force=True)
            await queue.close()

    async def test_worker_runs_automatic_refresh_check(self):
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        class Refresh:
            def __init__(self): self.called = asyncio.Event()
            async def check(self): self.called.set(); return {'created': []}
        refresh = Refresh()
        worker = create_worker(queue_name, test_redis_url(), object(), {}, refresh_scheduler=refresh)
        try:
            await queue.add('association_refresh_check', {})
            await asyncio.wait_for(refresh.called.wait(), timeout=5)
        finally:
            await worker.close()
            await queue.obliterate(force=True)
            await queue.close()
