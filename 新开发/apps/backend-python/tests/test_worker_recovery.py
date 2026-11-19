import asyncio
import json
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from bullmq import Queue

from baoxian_api.companies import MySQLCompanyService
from baoxian_api.tasks import MySQLTaskService
from baoxian_api.worker import create_worker
from test_company_api import test_database_url
from test_product_api import test_crawl_url
from test_queue import test_redis_url


class QueueProbe:
    def __init__(self, states=None):
        self.states = states or {}
        self.enqueued = []

    async def get_job_state(self, job_id):
        return self.states.get(job_id)

    async def enqueue(self, task_id):
        self.enqueued.append(task_id)


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.marker = 'python-recovery-' + uuid4().hex
        self.product_db = MySQLCompanyService(test_database_url())
        self.crawl_db = MySQLCompanyService(test_crawl_url())
        with self.product_db._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO company (name,official_site_url) VALUES (%s,%s)', (self.marker, 'https://example.test'))
                self.company_id = cursor.lastrowid
                cursor.execute("INSERT INTO product (company_id,name,normalized_name,sales_status,status_fetched_at,source_url,category_code,category_name,integration_status) VALUES (%s,%s,%s,'on_sale',UTC_TIMESTAMP(),'https://example.test','test','测试','running')", (self.company_id, self.marker, self.marker))
                self.product_id = cursor.lastrowid
        self.task_id = None

    async def asyncTearDown(self):
        if self.task_id is not None:
            with self.crawl_db._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
                    cursor.execute('DELETE FROM crawl_task WHERE id=%s', (self.task_id,))
        with self.product_db._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM product WHERE id=%s', (self.product_id,))
                cursor.execute('DELETE FROM company WHERE id=%s AND name=%s', (self.company_id, self.marker))

    def create_running_task(self, status='running'):
        scope = {
            'category': {'code': f'company:{self.company_id}', 'name': self.marker},
            'company': {'id': self.company_id, 'name': self.marker},
            'products': [{'id': self.product_id, 'name': self.marker}],
            '_integrationBefore': {str(self.product_id): {
                'integration_status': 'partial_failed',
                'integration_status_reason': '之前状态',
                'integration_last_task_id': None,
                'integration_status_updated_at': None,
            }},
        }
        with self.crawl_db._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents',%s,%s,%s)", (status, json.dumps(scope, ensure_ascii=False), '{}'))
                self.task_id = cursor.lastrowid
                cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,%s)", (self.task_id, f'company:{self.company_id}:product:{self.product_id}', 'running' if status == 'running' else 'pending'))
        with self.product_db._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE product SET integration_status='running',integration_status_reason=NULL,integration_last_task_id=%s WHERE id=%s", (self.task_id, self.product_id))

    async def test_missing_queued_job_is_reenqueued_after_redis_recovery(self):
        self.create_running_task(status='queued')
        queue = QueueProbe()
        service = MySQLTaskService(test_crawl_url(), queue=queue, product_database_url=test_database_url())

        result = await service.reconcile_worker_state()

        self.assertEqual(result['reenqueued'], 1)
        self.assertEqual(queue.enqueued, [self.task_id])
        self.assertEqual(service.get(self.task_id)['status'], 'queued')

    async def test_missing_running_job_marks_task_and_owned_product_failed(self):
        self.create_running_task()
        queue = QueueProbe()
        service = MySQLTaskService(test_crawl_url(), queue=queue, product_database_url=test_database_url())

        result = await service.reconcile_worker_state()

        task = service.get(self.task_id)
        self.assertEqual(result['failed'], 1)
        self.assertEqual((task['status'], task['units'][0]['state']), ('failed', 'failed'))
        self.assertIn('Worker 进程中断', task['units'][0]['lastError'])
        with self.product_db._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT integration_status,integration_last_task_id,integration_status_reason FROM product WHERE id=%s', (self.product_id,))
                product = cursor.fetchone()
        self.assertEqual(product['integration_status'], 'failed')
        self.assertEqual(product['integration_last_task_id'], self.task_id)
        self.assertIn('Worker 进程中断', product['integration_status_reason'])

    async def test_active_bullmq_job_keeps_running_task_for_takeover(self):
        self.create_running_task()
        queue = QueueProbe({f'crawl-{self.task_id}': 'active'})
        service = MySQLTaskService(test_crawl_url(), queue=queue, product_database_url=test_database_url())

        result = await service.reconcile_worker_state()

        self.assertEqual(result['failed'], 0)
        self.assertEqual(service.get(self.task_id)['status'], 'running')

    async def test_recovery_scheduler_job_runs_the_database_reconciliation(self):
        queue_name = 'python-recovery-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        called = AsyncMock(return_value={'reenqueued': 0, 'failed': 0, 'active': 0})
        class Tasks:
            async def reconcile_worker_state(inner_self):
                return await called()
        worker = create_worker(queue_name, test_redis_url(), Tasks(), {})
        try:
            await queue.add('task_state_recovery_check', {}, {'jobId': 'recovery-tick'})
            for _ in range(100):
                if called.await_count:
                    break
                await asyncio.sleep(.05)
            self.assertEqual(called.await_count, 1)
        finally:
            await worker.close()
            await queue.obliterate(force=True)
            await queue.close()


if __name__ == '__main__':
    unittest.main()
