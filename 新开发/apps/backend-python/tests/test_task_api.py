import json
import asyncio
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_product_api import test_crawl_url
from baoxian_api.app import create_app
from baoxian_api.tasks import MySQLTaskService


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.service = MySQLTaskService(test_crawl_url())
        self.conn = self.service._connect()
        self.cursor = self.conn.cursor()
        self.marker = 'python-task-' + uuid4().hex
        scope = {'category': {'code': self.marker, 'name': '测试'}, 'company': {'id': 998877, 'name': self.marker}, 'products': [{'id': 998878, 'name': self.marker}]}
        self.cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents','queued',%s,%s)", (json.dumps(scope, ensure_ascii=False), json.dumps({'globalConcurrency': 2})))
        self.task_id = self.cursor.lastrowid
        self.cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (self.task_id, self.marker))
        self.client = TestClient(create_app(internal_api_token='token', task_service=self.service))
        self.auth = {'Authorization': 'Bearer token'}

    def tearDown(self):
        self.client.close()
        self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (self.task_id,))
        self.cursor.execute('DELETE FROM crawl_task WHERE id = %s', (self.task_id,))
        self.cursor.close()
        self.conn.close()

    def test_coverage_reports_index_pages_and_failure_categories(self):
        scope = {'category': {'code': self.marker, 'name': '测试类别'}}
        self.cursor.execute("UPDATE crawl_task SET type='association_index',status='partial_failed',scope=%s,reported_total=12,read_count=10,unique_count=8 WHERE id=%s", (json.dumps(scope), self.task_id))
        self.cursor.execute("UPDATE crawl_task_unit SET state='completed' WHERE task_id=%s", (self.task_id,))
        self.cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,last_error) VALUES (%s,%s,2,'failed','连接超时')", (self.task_id, self.marker + ':2'))
        response = self.client.get('/api/reports/coverage', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()['items'] if item['taskId'] == self.task_id)
        self.assertEqual({key: row[key] for key in ('categoryCode', 'pageFrom', 'pageTo', 'reportedTotal', 'readCount', 'uniqueCount', 'duplicateCount', 'failedUnits')}, {'categoryCode': self.marker, 'pageFrom': 1, 'pageTo': 2, 'reportedTotal': 12, 'readCount': 10, 'uniqueCount': 8, 'duplicateCount': 2, 'failedUnits': 1})
        self.assertEqual(row['failures'], [{'page': 2, 'error': '连接超时', 'kind': 'timeout'}])
        self.assertEqual(self.client.get('/api/reports/coverage').status_code, 401)

    def test_list_filters_and_detail(self):
        response = self.client.get('/api/crawl-tasks', params={'type': 'company_documents', 'status': 'queued', 'companyId': 998877, 'productId': 998878, 'limit': 1}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        task = response.json()['items'][0]
        self.assertEqual(task['id'], self.task_id)
        self.assertEqual(task['scope']['category']['code'], self.marker)
        self.assertEqual(task['config']['globalConcurrency'], 2)
        self.assertTrue(task['createdAt'].endswith('Z'))
        detail = self.client.get(f'/api/crawl-tasks/{self.task_id}', headers=self.auth)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['units'][0]['unitKey'], self.marker)
        self.assertEqual(detail.json()['units'][0]['taskId'], self.task_id)
        self.assertEqual(self.client.get('/api/crawl-tasks/999999999999', headers=self.auth).status_code, 404)

    def test_task_list_offset_returns_disjoint_pages_and_has_more(self):
        scope = {'category': {'code': self.marker, 'name': '测试'}, 'company': {'id': 998877, 'name': self.marker}, 'products': []}
        self.cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents','queued',%s,%s)", (json.dumps(scope, ensure_ascii=False), json.dumps({'globalConcurrency': 2})))
        second_id = self.cursor.lastrowid
        try:
            first = self.client.get('/api/crawl-tasks', params={'type': 'company_documents', 'limit': 1, 'offset': 0}, headers=self.auth)
            second = self.client.get('/api/crawl-tasks', params={'type': 'company_documents', 'limit': 1, 'offset': 1}, headers=self.auth)
            self.assertEqual((first.status_code, second.status_code), (200, 200))
            self.assertEqual((first.json()['total'], first.json()['hasMore']), (2, True))
            self.assertEqual((second.json()['total'], second.json()['hasMore']), (2, False))
            first_id = first.json()['items'][0]['id']
            next_id = second.json()['items'][0]['id']
            self.assertNotEqual(first_id, next_id)
            self.assertEqual({first_id, next_id}, {self.task_id, second_id})
        finally:
            self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (second_id,))
            self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (second_id,))

    def test_running_task_events_emit_changes_and_terminal_event(self):
        class Tasks:
            def __init__(self): self.calls = 0
            def get(self, task_id):
                self.calls += 1
                return {'id': task_id, 'type': 'association_index', 'status': 'completed' if self.calls >= 3 else 'running', 'readCount': 2 if self.calls >= 3 else 1}
        tasks = Tasks()
        with TestClient(create_app(internal_api_token='token', task_service=tasks)) as client:
            response = client.get('/api/crawl-tasks/17/events', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        blocks = response.text.strip().split('\n\n')
        self.assertEqual([block.splitlines()[0] for block in blocks], ['event: task', 'event: task', 'event: done'])
        self.assertEqual(json.loads(blocks[0].splitlines()[1][6:])['status'], 'running')
        self.assertEqual(json.loads(blocks[1].splitlines()[1][6:])['status'], 'completed')
        self.assertEqual(json.loads(blocks[2].splitlines()[1][6:]), {'status': 'completed'})

    def test_terminal_task_events_emit_task_and_done_then_close(self):
        self.cursor.execute("UPDATE crawl_task SET status = 'failed' WHERE id = %s", (self.task_id,))
        response = self.client.get(f'/api/crawl-tasks/{self.task_id}/events', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
        blocks = response.text.strip().split('\n\n')
        self.assertEqual(blocks[0].splitlines()[0], 'event: task')
        self.assertEqual(json.loads(blocks[0].splitlines()[1][6:])['id'], self.task_id)
        self.assertEqual(blocks[1].splitlines()[0], 'event: done')
        self.assertEqual(json.loads(blocks[1].splitlines()[1][6:]), {'status': 'failed'})

    def test_association_detail_includes_confirmed_product_values(self):
        scope = {'category': {'code': self.marker, 'name': '测试'}}
        self.cursor.execute("UPDATE crawl_task SET type = 'association_index', scope = %s WHERE id = %s", (json.dumps(scope), self.task_id))
        self.cursor.execute('INSERT INTO crawl_task_product (task_id, product_id) VALUES (%s, %s)', (self.task_id, 998878))
        class Products:
            def list(self):
                return [{'id': 998878, 'name': '来源名', 'confirmedName': '确认名', 'salesStatus': 'on_sale', 'confirmedSalesStatus': 'off_sale', 'companyName': '测试公司'}]
        try:
            with TestClient(create_app(internal_api_token='token', task_service=self.service, product_service=Products())) as client:
                response = client.get(f'/api/crawl-tasks/{self.task_id}', headers=self.auth)
            self.assertEqual(response.json()['products'], [{'id': 998878, 'name': '确认名', 'companyName': '测试公司', 'salesStatus': 'off_sale'}])
        finally:
            self.cursor.execute('DELETE FROM crawl_task_product WHERE task_id = %s', (self.task_id,))

    def test_company_discovery_detail_includes_current_site_and_unit(self):
        scope = {'category': {'code': 'company_site', 'name': '公司官网发现'}, 'companies': [{'id': 998877, 'name': '测试公司'}]}
        self.cursor.execute("UPDATE crawl_task SET type = 'company_site_discovery', scope = %s WHERE id = %s", (json.dumps(scope, ensure_ascii=False), self.task_id))
        self.cursor.execute("UPDATE crawl_task_unit SET unit_key = 'company-site:998877', state = 'failed', last_error = '网络失败' WHERE task_id = %s", (self.task_id,))
        class Companies:
            def list(self):
                return [{'id': 998877, 'officialSiteUrl': 'https://example.com'}]
        with TestClient(create_app(internal_api_token='token', task_service=self.service, company_service=Companies())) as client:
            response = client.get(f'/api/crawl-tasks/{self.task_id}', headers=self.auth)
        self.assertEqual(response.json()['companySites'], [{'companyId': 998877, 'companyName': '测试公司', 'currentSiteUrl': 'https://example.com', 'state': 'failed', 'reason': '网络失败'}])

    def test_create_index_task_saves_settings_and_enqueues(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True, 'indexRefreshMinutes': 10080}
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            before = datetime.now(timezone.utc).timestamp()
            with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
                response = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'PubNewProdTypeCode_00', 'maxPages': 2, 'saleAfter': '2026-01-01'})
            self.assertEqual(response.status_code, 202)
            task = response.json()
            created_at = datetime.fromisoformat(task['createdAt'].replace('Z', '+00:00')).timestamp()
            self.assertGreaterEqual(created_at, before - 3)
            self.assertLessEqual(created_at, datetime.now(timezone.utc).timestamp() + 3)
            self.assertEqual(queue.ids, [task['id']])
            self.assertEqual(task['scope'], {'category': {'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'}, 'maxPages': 2, 'saleAfter': '2026-01-01'})
            self.assertEqual(task['config'], {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True})
            self.assertEqual((task['units'][0]['unitKey'], task['units'][0]['state']), ('PubNewProdTypeCode_00:1', 'pending'))
        finally:
            for task_id in queue.ids:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (task_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id = %s', (task_id,))

    def test_scheduled_task_is_persisted_without_enqueue(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        service = MySQLTaskService(test_crawl_url(), settings=Settings())
        task = asyncio.run(service.create_index_task({'code': self.marker, 'name': '测试'}, enqueue=False))
        try:
            self.assertEqual(task['status'], 'queued')
            self.assertEqual(task['config']['retryAttempts'], 3)
            self.assertEqual(task['units'][0]['unitKey'], self.marker + ':1')
        finally:
            self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (task['id'],))
            self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task['id'],))

    def test_disabled_crawl_and_invalid_category_do_not_enqueue(self):
        class Settings:
            def get(self): return {'associationCrawlEnabled': False}
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
            disabled = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'PubNewProdTypeCode_00'})
            unknown = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'unknown'})
            invalid = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'PubNewProdTypeCode_00', 'saleAfter': '2026-02-30'})
        self.assertEqual((disabled.status_code, disabled.json()['code']), (409, 'association_crawl_disabled'))
        self.assertEqual((unknown.status_code, unknown.json()['code']), (400, 'unknown_category'))
        self.assertEqual((invalid.status_code, invalid.json()['code']), (400, 'validation_error'))
        self.assertEqual(queue.ids, [])

    def test_queue_failure_marks_created_task_failed(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id):
                self.ids.append(task_id)
                raise OSError('queue unavailable')
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
                response = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'PubNewProdTypeCode_00'})
            self.assertEqual((response.status_code, response.json()['code']), (503, 'queue_unavailable'))
            self.assertEqual(service.get(queue.ids[0])['status'], 'failed')
        finally:
            for task_id in queue.ids:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (task_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id = %s', (task_id,))

    def test_unconfirmed_queue_write_stays_queued_for_worker_recovery(self):
        class Settings:
            def get(self):
                return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class UnavailableQueue:
            task_id = None
            async def enqueue(self, task_id):
                self.task_id = task_id
                raise TimeoutError('Redis 写入确认丢失')
            async def contains_task(self, task_id):
                raise TimeoutError('Redis 暂不可查询')
        queue = UnavailableQueue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
                response = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'association_index', 'category': 'PubNewProdTypeCode_00'})
            self.assertEqual((response.status_code, response.json()['code']), (503, 'queue_unavailable'))
            self.assertEqual(service.get(queue.task_id)['status'], 'queued')
        finally:
            if queue.task_id:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (queue.task_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (queue.task_id,))

    def test_cancel_queued_task_and_reject_completed_or_cancelled(self):
        response = self.client.post(f'/api/crawl-tasks/{self.task_id}/cancel', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertIsNotNone(response.json()['finishedAt'])
        second = self.client.post(f'/api/crawl-tasks/{self.task_id}/cancel', headers=self.auth)
        self.assertEqual((second.status_code, second.json()['code']), (409, 'task_not_controllable'))
        self.cursor.execute("UPDATE crawl_task SET status = 'completed' WHERE id = %s", (self.task_id,))
        completed = self.client.post(f'/api/crawl-tasks/{self.task_id}/cancel', headers=self.auth)
        self.assertEqual(completed.status_code, 409)
        missing = self.client.post('/api/crawl-tasks/999999999999/cancel', headers=self.auth)
        self.assertEqual(missing.status_code, 404)

    def test_cancel_does_not_overwrite_concurrent_completion(self):
        original_connect = self.service._connect
        outer = self
        class Cursor:
            def __init__(self, inner): self.inner = inner
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def __getattr__(self, name): return getattr(self.inner, name)
            def execute(self, sql, args=None):
                if sql.startswith("UPDATE crawl_task SET status = 'cancelled'"):
                    outer.cursor.execute("UPDATE crawl_task SET status='completed' WHERE id=%s", (outer.task_id,))
                return self.inner.execute(sql, args)
        class Connection:
            def __init__(self, inner): self.inner = inner
            def __enter__(self): self.inner.__enter__(); return self
            def __exit__(self, *args): return self.inner.__exit__(*args)
            def __getattr__(self, name): return getattr(self.inner, name)
            def cursor(self): return Cursor(self.inner.cursor())
        with patch.object(self.service, '_connect', side_effect=lambda: Connection(original_connect())):
            response = self.client.post(f'/api/crawl-tasks/{self.task_id}/cancel', headers=self.auth)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'task_not_controllable'))
        self.assertEqual(self.service.get(self.task_id)['status'], 'completed')

    def test_queue_failure_does_not_overwrite_concurrent_cancel(self):
        self.cursor.execute("UPDATE crawl_task SET status='failed' WHERE id=%s", (self.task_id,))
        class Queue:
            async def enqueue(_, task_id):
                self.service.cancel(task_id)
                raise RuntimeError('队列不可用')
        self.service.queue = Queue()
        response = self.client.post(f'/api/crawl-tasks/{self.task_id}/retry', headers=self.auth)
        self.assertEqual((response.status_code, response.json()['code']), (503, 'queue_unavailable'))
        self.assertEqual(self.service.get(self.task_id)['status'], 'cancelled')

    def test_resume_queue_failure_does_not_leave_task_queued(self):
        self.cursor.execute("UPDATE crawl_task SET status='cancelled' WHERE id=%s", (self.task_id,))
        class Queue:
            async def enqueue(_, task_id): raise RuntimeError('队列不可用')
        self.service.queue = Queue()
        response = self.client.post(f'/api/crawl-tasks/{self.task_id}/resume', headers=self.auth)
        self.assertEqual((response.status_code, response.json()['code']), (503, 'queue_unavailable'))
        self.assertEqual(self.service.get(self.task_id)['status'], 'failed')

    def test_retry_failed_child_queue_failure_marks_child_failed(self):
        self.cursor.execute("UPDATE crawl_task_unit SET state='failed' WHERE task_id=%s", (self.task_id,))
        class Queue:
            child_id = None
            async def enqueue(queue, task_id):
                queue.child_id = task_id
                raise RuntimeError('队列不可用')
        queue = Queue()
        self.service.queue = queue
        try:
            response = self.client.post(f'/api/crawl-tasks/{self.task_id}/retry-failed', headers=self.auth)
            self.assertEqual((response.status_code, response.json()['code']), (503, 'queue_unavailable'))
            self.assertIsNotNone(queue.child_id)
            self.assertEqual(self.service.get(queue.child_id)['status'], 'failed')
        finally:
            if queue.child_id is not None:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (queue.child_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (queue.child_id,))

    def test_retry_and_resume_roll_back_unit_reset_when_task_update_fails(self):
        for action in ('retry', 'resume'):
            with self.subTest(action=action):
                self.cursor.execute("UPDATE crawl_task SET status='failed' WHERE id=%s", (self.task_id,))
                self.cursor.execute("UPDATE crawl_task_unit SET state='failed',last_error='original error' WHERE task_id=%s", (self.task_id,))
                original_connect = self.service._connect
                class Cursor:
                    def __init__(self, inner): self.inner = inner
                    def __enter__(self): self.inner.__enter__(); return self
                    def __exit__(self, *args): return self.inner.__exit__(*args)
                    def __getattr__(self, name): return getattr(self.inner, name)
                    def execute(self, sql, args=None):
                        if sql.startswith("UPDATE crawl_task SET status") and "'queued'" in sql:
                            raise RuntimeError('injected task update failure')
                        return self.inner.execute(sql, args)
                class Connection:
                    def __init__(self, inner): self.inner = inner
                    def __enter__(self): self.inner.__enter__(); return self
                    def __exit__(self, *args): return self.inner.__exit__(*args)
                    def __getattr__(self, name): return getattr(self.inner, name)
                    def cursor(self): return Cursor(self.inner.cursor())
                with patch.object(self.service, '_connect', side_effect=lambda: Connection(original_connect())):
                    with self.assertRaisesRegex(RuntimeError, 'injected task update failure'):
                        asyncio.run(getattr(self.service, action)(self.task_id))
                self.cursor.execute('SELECT state,last_error FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
                self.assertEqual(self.cursor.fetchone(), {'state': 'failed', 'last_error': 'original error'})
                self.assertEqual(self.service.get(self.task_id)['status'], 'failed')

    def test_concurrent_retries_enqueue_only_once(self):
        self.cursor.execute("UPDATE crawl_task SET status='failed' WHERE id=%s", (self.task_id,))
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        self.service.queue = queue
        async def retry_together():
            return await asyncio.gather(self.service.retry(self.task_id), self.service.retry(self.task_id), return_exceptions=True)
        results = asyncio.run(retry_together())
        from baoxian_api.tasks import TaskNotControllableError
        self.assertEqual(queue.ids, [self.task_id])
        self.assertEqual(sum(isinstance(value, TaskNotControllableError) for value in results), 1)
        self.assertEqual(sum(isinstance(value, dict) and value['status'] == 'queued' for value in results), 1)

    def test_retry_failed_task_resets_failed_units_and_requeues(self):
        self.cursor.execute("UPDATE crawl_task SET status = 'failed', started_at = UTC_TIMESTAMP(), finished_at = UTC_TIMESTAMP() WHERE id = %s", (self.task_id,))
        self.cursor.execute("UPDATE crawl_task_unit SET state = 'failed', last_error = 'timeout' WHERE task_id = %s", (self.task_id,))
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), queue=queue)
        with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
            response = client.post(f'/api/crawl-tasks/{self.task_id}/retry', headers=self.auth)
            self.assertEqual(response.status_code, 200)
            task = response.json()
            self.assertEqual((task['status'], task['startedAt'], task['finishedAt']), ('queued', None, None))
            self.assertEqual((task['units'][0]['state'], task['units'][0]['lastError']), ('pending', None))
            self.assertEqual(queue.ids, [self.task_id])
            self.assertEqual(client.post(f'/api/crawl-tasks/{self.task_id}/retry', headers=self.auth).status_code, 409)

    def test_create_company_document_task_and_convenience_route(self):
        class Settings:
            def get(self): return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        class Companies:
            def list(self): return [{'id': 20, 'name': '测试公司', 'officialSiteUrl': 'https://example.com', 'documentEntryUrl': 'https://example.com/list'}]
        class Products:
            def list(self, company_id=None): return [
                {'id': 101, 'name': '产品甲', 'salesStatus': 'stopped'},
                {'id': 102, 'name': '产品乙', 'salesStatus': 'on_sale'},
            ] if company_id == 20 else []
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service, company_service=Companies(), product_service=Products())) as client:
                selected = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'company_documents', 'companyId': 20, 'productIds': [102, 102]})
                self.assertEqual(selected.status_code, 202)
                task = selected.json()
                self.assertEqual(task['type'], 'company_documents')
                self.assertEqual(task['scope']['company']['documentEntryUrl'], 'https://example.com/list')
                self.assertEqual(task['scope']['products'], [{'id': 102, 'name': '产品乙', 'salesStatus': 'on_sale'}])
                self.assertEqual([unit['unitKey'] for unit in task['units']], ['company:20:product:102'])
                shortcut = client.post('/api/companies/20/document-tasks', headers=self.auth, json={})
                self.assertEqual(shortcut.status_code, 202)
                self.assertEqual(len(shortcut.json()['scope']['products']), 2)
                bad = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'company_documents', 'companyId': 20, 'productIds': [999]})
                self.assertEqual((bad.status_code, bad.json()['code']), (400, 'unknown_product'))
            self.assertEqual(len(queue.ids), 2)
        finally:
            for task_id in queue.ids:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (task_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id = %s', (task_id,))

    def test_company_task_scope_keeps_effective_product_status(self):
        class Settings:
            def get(self): return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class Queue:
            async def enqueue(self, task_id): pass
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=Queue())
        task = asyncio.run(service.create_company_task(
            {'id': 20, 'name': '测试公司', 'officialSiteUrl': 'https://example.com'},
            [{'id': 101, 'name': '协会产品名', 'confirmedName': '确认产品名', 'salesStatus': 'on_sale', 'confirmedSalesStatus': 'stopped'}],
        ))
        try:
            self.assertEqual(task['scope']['products'], [{'id': 101, 'name': '确认产品名', 'salesStatus': 'stopped'}])
        finally:
            self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (task['id'],))
            self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task['id'],))

    def test_company_document_task_without_official_site_is_rejected_before_enqueue(self):
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        class Companies:
            def list(self): return [{'id': 20, 'name': '未配置官网公司', 'officialSiteUrl': None}]
        class Products:
            def list(self, company_id=None): raise AssertionError('拒绝请求前不应读取产品')
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), queue=queue)
        with TestClient(create_app(internal_api_token='token', task_service=service, company_service=Companies(), product_service=Products())) as client:
            response = client.post('/api/companies/20/document-tasks', headers=self.auth, json={})
        self.assertEqual((response.status_code, response.json()['code']), (400, 'company_site_missing'))
        self.assertEqual(queue.ids, [])

    def test_create_site_discovery_only_for_companies_without_existing_site(self):
        class Settings:
            def get(self): return {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'associationCrawlEnabled': True}
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        class Companies:
            def list(self): return [{'id': 20, 'name': '待发现甲', 'officialSiteUrl': None}, {'id': 21, 'name': '待发现乙', 'officialSiteUrl': ''}, {'id': 22, 'name': '已配置', 'officialSiteUrl': 'https://example.com'}]
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), settings=Settings(), queue=queue)
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service, company_service=Companies())) as client:
                response = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'company_site_discovery'})
                self.assertEqual(response.status_code, 202)
                task = response.json()
                self.assertEqual(task['type'], 'company_site_discovery')
                self.assertEqual([item['id'] for item in task['scope']['companies']], [20, 21])
                self.assertEqual({item['unitKey'] for item in task['units']}, {'company-site:20', 'company-site:21'})
                existing = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'company_site_discovery', 'companyIds': [22]})
                self.assertEqual((existing.status_code, existing.json()['code']), (400, 'company_site_exists'))
                missing = client.post('/api/crawl-tasks', headers=self.auth, json={'type': 'company_site_discovery', 'companyIds': [999]})
                self.assertEqual((missing.status_code, missing.json()['code']), (400, 'unknown_company'))
            self.assertEqual(queue.ids, [task['id']])
        finally:
            for task_id in queue.ids:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (task_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id = %s', (task_id,))

    def test_resume_cancelled_task_preserves_completed_units(self):
        self.cursor.execute("UPDATE crawl_task SET status='cancelled', started_at=UTC_TIMESTAMP(), finished_at=UTC_TIMESTAMP() WHERE id=%s", (self.task_id,))
        self.cursor.execute("UPDATE crawl_task_unit SET state='completed' WHERE task_id=%s", (self.task_id,))
        self.cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,last_error) VALUES (%s,%s,2,'running','interrupted')", (self.task_id, self.marker + ':2'))
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), queue=queue)
        with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
            response = client.post(f'/api/crawl-tasks/{self.task_id}/resume', headers=self.auth)
            self.assertEqual(response.status_code, 200)
            task = response.json()
            self.assertEqual((task['status'], task['startedAt'], task['finishedAt']), ('queued', None, None))
            self.assertEqual([unit['state'] for unit in task['units']], ['completed', 'pending'])
            self.assertIsNone(task['units'][1]['lastError'])
            self.assertEqual(queue.ids, [self.task_id])
            self.assertEqual(client.post(f'/api/crawl-tasks/{self.task_id}/resume', headers=self.auth).status_code, 409)

    def test_retry_failed_creates_child_with_only_failed_company_products(self):
        scope = {'category': {'code': 'company:20', 'name': '测试公司'}, 'company': {'id': 20, 'name': '测试公司'}, 'products': [{'id': 101, 'name': '甲'}, {'id': 102, 'name': '乙'}]}
        self.cursor.execute("UPDATE crawl_task SET status='partial_failed',scope=%s WHERE id=%s", (json.dumps(scope, ensure_ascii=False), self.task_id))
        self.cursor.execute("UPDATE crawl_task_unit SET unit_key='company:20:product:101',state='completed' WHERE task_id=%s", (self.task_id,))
        self.cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,\x60cursor\x60,last_error) VALUES (%s,'company:20:product:102',2,'failed','p2','失败')", (self.task_id,))
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), queue=queue)
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
                response = client.post(f'/api/crawl-tasks/{self.task_id}/retry-failed', headers=self.auth)
                self.assertEqual(response.status_code, 200)
                child = response.json()
                self.assertEqual(child['parentTaskId'], self.task_id)
                self.assertEqual(child['scope']['products'], [{'id': 102, 'name': '乙'}])
                self.assertEqual((child['units'][0]['unitKey'], child['units'][0]['page'], child['units'][0]['state'], child['units'][0]['cursor']), ('company:20:product:102', 2, 'pending', 'p2'))
                self.assertEqual(queue.ids, [child['id']])
        finally:
            for child_id in queue.ids:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (child_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (child_id,))

    def test_retry_failed_site_discovery_includes_only_failed_companies(self):
        scope = {'category': {'code': 'company_site', 'name': '公司官网发现'}, 'companies': [{'id': 101, 'name': '失败公司'}, {'id': 102, 'name': '已完成公司'}]}
        self.cursor.execute("UPDATE crawl_task SET type='company_site_discovery',scope=%s WHERE id=%s", (json.dumps(scope, ensure_ascii=False), self.task_id))
        self.cursor.execute("UPDATE crawl_task_unit SET unit_key='company-site:101',state='failed',last_error='网络失败' WHERE task_id=%s", (self.task_id,))
        self.cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,'company-site:102',1,'completed')", (self.task_id,))
        class Queue:
            def __init__(self): self.ids = []
            async def enqueue(self, task_id): self.ids.append(task_id)
        queue = Queue()
        service = MySQLTaskService(test_crawl_url(), queue=queue)
        child_id = None
        try:
            with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
                response = client.post(f'/api/crawl-tasks/{self.task_id}/retry-failed', headers=self.auth)
            self.assertEqual(response.status_code, 200)
            child = response.json()
            child_id = child['id']
            self.assertEqual(child['scope']['companies'], [{'id': 101, 'name': '失败公司'}])
            self.assertEqual([(unit['unitKey'], unit['state']) for unit in child['units']], [('company-site:101', 'pending')])
            self.assertEqual(queue.ids, [child_id])
        finally:
            if child_id:
                self.cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (child_id,))
                self.cursor.execute('DELETE FROM crawl_task WHERE id=%s', (child_id,))

    def test_retry_failed_rejects_tasks_without_failed_units(self):
        class Queue:
            async def enqueue(self, task_id): raise AssertionError('不应入队')
        service = MySQLTaskService(test_crawl_url(), queue=Queue())
        with TestClient(create_app(internal_api_token='token', task_service=service)) as client:
            response = client.post(f'/api/crawl-tasks/{self.task_id}/retry-failed', headers=self.auth)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'task_not_controllable'))
