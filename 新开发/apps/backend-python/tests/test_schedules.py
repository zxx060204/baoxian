import asyncio
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from baoxian_api.app import create_app
from baoxian_api.schedules import MySQLScheduleService, DuplicateScheduleError
from test_product_api import test_crawl_url


class ScheduleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_lock_orders_creation_before_concurrent_disable(self):
        class Queue:
            async def upsert_schedule(self, *args): pass
            async def remove_schedule(self, *args): pass
        service = MySQLScheduleService(test_crawl_url(), Queue())
        category = {'code': 'test-' + uuid4().hex, 'name': '测试'}
        schedule = await service.create(category, 60, True)
        entered, release = asyncio.Event(), asyncio.Event()
        async def create_task():
            entered.set()
            await release.wait()
            return {'id': 42}
        run = None
        disable = None
        try:
            run = asyncio.create_task(service.run_if_enabled(schedule['id'], category['code'], create_task))
            await asyncio.wait_for(entered.wait(), 3)
            disable = asyncio.create_task(service.update(schedule['id'], {'enabled': False}))
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(disable), .2)
            release.set()
            self.assertEqual(await asyncio.wait_for(run, 3), {'id': 42})
            self.assertFalse((await asyncio.wait_for(disable, 3))['enabled'])
            called = False
            async def should_not_run():
                nonlocal called
                called = True
            self.assertIsNone(await service.run_if_enabled(schedule['id'], category['code'], should_not_run))
            self.assertFalse(called)
        finally:
            release.set()
            await asyncio.gather(*([run] if run else []), *([disable] if disable else []), return_exceptions=True)
            with service._connect() as conn:
                with conn.cursor() as cursor: cursor.execute('DELETE FROM crawl_schedule WHERE id=%s AND category_code=%s', (schedule['id'], category['code']))

    async def test_schedule_lock_creates_actual_index_task(self):
        from baoxian_api.tasks import MySQLTaskService
        class Queue:
            async def upsert_schedule(self, *args): pass
            async def remove_schedule(self, *args): pass
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        service = MySQLScheduleService(test_crawl_url(), Queue())
        tasks = MySQLTaskService(test_crawl_url(), settings=Settings())
        category = {'code': 'test-' + uuid4().hex, 'name': '测试'}
        schedule = await service.create(category, 60, True)
        task = None
        try:
            async def create_task():
                return await tasks.create_index_task(category, enqueue=False)
            task = await service.run_if_enabled(schedule['id'], category['code'], create_task)
            self.assertEqual(task['status'], 'queued')
            self.assertEqual(task['scope']['category'], category)
        finally:
            with service._connect() as conn:
                with conn.cursor() as cursor:
                    if task is not None:
                        cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (task['id'],))
                        cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task['id'],))
                    cursor.execute('DELETE FROM crawl_schedule WHERE id=%s AND category_code=%s', (schedule['id'], category['code']))

    async def test_create_update_sync_and_duplicate(self):
        class Queue:
            def __init__(self): self.schedules = {}
            async def upsert_schedule(self, schedule_id, code, minutes): self.schedules[schedule_id] = (code, minutes)
            async def remove_schedule(self, schedule_id): self.schedules.pop(schedule_id, None)
        queue = Queue()
        service = MySQLScheduleService(test_crawl_url(), queue)
        category = {'code': 'test-' + uuid4().hex, 'name': '测试'}
        schedule = await service.create(category, 60, True)
        try:
            self.assertEqual(queue.schedules[schedule['id']], (category['code'], 60))
            with self.assertRaises(DuplicateScheduleError): await service.create(category, 30, True)
            disabled = await service.update(schedule['id'], {'enabled': False})
            self.assertFalse(disabled['enabled'])
            self.assertNotIn(schedule['id'], queue.schedules)
            await service.update(schedule['id'], {'enabled': True, 'everyMinutes': 30})
            self.assertEqual(queue.schedules[schedule['id']], (category['code'], 30))
        finally:
            with service._connect() as conn:
                with conn.cursor() as cursor: cursor.execute('DELETE FROM crawl_schedule WHERE id=%s AND category_code=%s', (schedule['id'], category['code']))


class ScheduleApiTests(unittest.TestCase):
    def test_create_list_update_and_validation(self):
        class Service:
            def __init__(self): self.items = []
            async def create(self, category, every_minutes, enabled):
                item = {'id': 1, 'categoryCode': category['code'], 'categoryName': category['name'], 'everyMinutes': every_minutes, 'enabled': enabled}; self.items.append(item); return item
            async def update(self, schedule_id, changes): self.items[0].update(changes); return self.items[0]
            async def list(self): return self.items
        with TestClient(create_app(internal_api_token='token', schedule_service=Service())) as client:
            headers = {'Authorization': 'Bearer token'}
            created = client.post('/api/schedules', headers=headers, json={'category': 'PubNewProdTypeCode_00', 'everyMinutes': 60})
            self.assertEqual(created.status_code, 201)
            self.assertTrue(created.json()['enabled'])
            self.assertFalse(client.patch('/api/schedules/1', headers=headers, json={'enabled': False}).json()['enabled'])
            self.assertEqual(len(client.get('/api/schedules', headers=headers).json()['items']), 1)
            self.assertEqual(client.post('/api/schedules', headers=headers, json={'category': 'bad', 'everyMinutes': 60}).json()['code'], 'unknown_category')
            self.assertEqual(client.post('/api/schedules', headers=headers, json={'category': 'PubNewProdTypeCode_00', 'everyMinutes': 0}).status_code, 400)
            self.assertEqual(client.patch('/api/schedules/1', headers=headers, json={}).status_code, 400)

    def test_startup_sync_failure_does_not_prevent_api_start(self):
        class Service:
            def __init__(self): self.called = False
            async def sync_schedulers(self): self.called = True; raise RuntimeError('队列不可用')
        service = Service()
        with self.assertLogs('baoxian_api.app', level='ERROR'):
            with TestClient(create_app(internal_api_token='token', schedule_service=service)) as client:
                self.assertEqual(client.get('/health').status_code, 200)
        self.assertTrue(service.called)
