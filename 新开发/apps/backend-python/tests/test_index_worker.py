import asyncio
import json
import os
import unittest
from pathlib import Path

import httpx
from baoxian_api.association import AssociationAdapter, AssociationGateway
from uuid import uuid4

from test_company_api import test_database_url
from test_product_api import test_crawl_url
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.ingestion import ProductIngestionService
from baoxian_api.review_reporter import MySQLReviewReporter
from baoxian_api.tasks import MySQLTaskService
from baoxian_api.index_worker import IndexTaskProcessor, create_index_worker
from test_queue import test_redis_url
from bullmq import Queue


class IndexWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product = MySQLCompanyService(test_database_url())._connect()
        self.pc = self.product.cursor()
        self.crawl = MySQLCompanyService(test_crawl_url())._connect()
        self.cc = self.crawl.cursor()
        self.marker = 'python-index-' + uuid4().hex
        self.category = {'code': self.marker, 'name': '测试类别'}
        self.scope = {'category': self.category}
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps(self.scope), json.dumps({'retryAttempts': 1, 'requestTimeoutMs': 3000})))
        self.task_id = self.cc.lastrowid
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (self.task_id, self.marker + ':1'))
        self.tasks = MySQLTaskService(test_crawl_url())
        self.ingestion = ProductIngestionService(test_database_url(), MySQLReviewReporter(test_crawl_url()))
        self.review = MySQLReviewReporter(test_crawl_url())

    async def asyncTearDown(self):
        self.cc.execute('DELETE FROM review_item WHERE task_id = %s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task_product WHERE task_id = %s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id = %s', (self.task_id,))
        self.cc.execute('DELETE FROM crawl_task WHERE id = %s', (self.task_id,))
        self.cc.execute('DELETE FROM association_index_progress WHERE category_code = %s', (self.marker,))
        self.pc.execute('SELECT id FROM company WHERE name = %s', (self.marker,))
        company = self.pc.fetchone()
        if company:
            self.pc.execute('DELETE FROM product_index_record WHERE company_id = %s', (company['id'],))
            self.pc.execute('DELETE FROM product WHERE company_id = %s', (company['id'],))
            self.pc.execute('DELETE FROM company WHERE id = %s', (company['id'],))
        self.cc.close(); self.crawl.close(); self.pc.close(); self.product.close()

    def record(self, name, ident):
        return {'companyName': self.marker, 'productName': name, 'salesStatus': 'on_sale', 'salesStatusRaw': '在售', 'sourceUrl': 'https://example.test', 'sourceRecordId': ident, 'fetchedAt': '2026-11-07T05:00:00.000Z'}

    async def test_runtime_fixture_mode_consumes_queue_and_persists_product(self):
        from unittest.mock import patch
        from baoxian_api.fixture_association import RECORDS
        from baoxian_api.worker_runtime import create_runtime
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        records = [{'companyName': self.marker, 'productName': '样本保险', 'salesStatusRaw': '在售', 'recordId': 'fixture-1'}]
        def forbidden(request): raise AssertionError('样本模式不应请求网络')
        try:
            with patch.dict(RECORDS, {self.marker: records}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
                    runtime = await create_runtime({'DATABASE_URL': test_database_url(), 'CRAWL_DATABASE_URL': test_crawl_url(), 'REDIS_URL': test_redis_url(), 'CRAWL_QUEUE_NAME': queue_name, 'ASSOCIATION_SOURCE': 'fixture'}, client=client, enable_refresh=False)
                    try:
                        await queue.add('association_index', {'taskId': self.task_id})
                        async def wait_for_task():
                            while True:
                                task = await asyncio.to_thread(self.tasks.get, self.task_id)
                                if task['status'] in ('completed', 'failed', 'partial_failed', 'cancelled'): return task
                                await asyncio.sleep(.05)
                        task = await asyncio.wait_for(wait_for_task(), 10)
                        self.assertEqual((task['status'], task['readCount'], task['uniqueCount'], task['reportedTotal']), ('completed', 1, 1, 1))
                        self.pc.execute('SELECT name,association_record_id FROM product WHERE company_id=(SELECT id FROM company WHERE name=%s)', (self.marker,))
                        self.assertEqual(self.pc.fetchone(), {'name': '样本保险', 'association_record_id': 'fixture-1'})
                    finally:
                        await runtime.close()
        finally:
            await queue.obliterate(force=True)
            await queue.close()

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
        class Adapter:
            async def read_page(self, category, cursor=None):
                raise AssertionError('已取消任务不应请求协会')
        processor = IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        processor.tasks = RacingTasks()
        await processor.process(self.task_id)
        self.assertEqual(tasks.get(self.task_id)['status'], 'cancelled')

    async def test_cancel_after_final_read_is_not_overwritten_by_completion(self):
        tasks = self.tasks
        class RacingTasks:
            def __init__(self): self.calls = 0
            def get(self, task_id):
                task = tasks.get(task_id)
                self.calls += 1
                if self.calls == 4: tasks.cancel(task_id)
                return task
        class Adapter:
            async def read_page(self, category, cursor=None): return {'products': [], 'reportedTotal': 0}
        processor = IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        processor.tasks = RacingTasks()
        await processor.process(self.task_id)
        self.assertEqual(tasks.get(self.task_id)['status'], 'cancelled')
        self.cc.execute('SELECT completed_at FROM association_index_progress WHERE category_code=%s', (self.marker,))
        progress = self.cc.fetchone()
        self.assertTrue(progress is None or progress['completed_at'] is None)

    async def test_worker_runtime_in_subprocess_consumes_and_closes(self):
        import subprocess
        import sys
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        env = dict(os.environ, DATABASE_URL=test_database_url(), CRAWL_DATABASE_URL=test_crawl_url(), REDIS_URL=test_redis_url(), CRAWL_QUEUE_NAME=queue_name, ASSOCIATION_SOURCE='fixture')
        code = "import asyncio,os,sys; from baoxian_api.worker_runtime import create_runtime\nasync def main():\n runtime=await create_runtime(os.environ,enable_refresh=False)\n print('ready',flush=True)\n try: await asyncio.to_thread(sys.stdin.readline)\n finally: await runtime.close()\nasyncio.run(main())"
        process = None
        try:
            process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual((await asyncio.wait_for(process.stdout.readline(), 15)).strip(), b'ready')
            await queue.add('association_index', {'taskId': self.task_id})
            async def wait_done():
                while True:
                    if process.returncode is not None: raise AssertionError('Worker 进程提前退出')
                    task = await asyncio.to_thread(self.tasks.get, self.task_id)
                    if task['status'] in ('completed', 'failed', 'partial_failed', 'cancelled'): return task
                    await asyncio.sleep(.05)
            task = await asyncio.wait_for(wait_done(), 15)
            self.assertEqual((task['status'], task['readCount'], task['reportedTotal']), ('completed', 0, 0))
            process.stdin.write(b'stop\n')
            await process.stdin.drain()
            await asyncio.wait_for(process.wait(), 10)
            self.assertEqual(process.returncode, 0)
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
            await queue.obliterate(force=True)
            await queue.close()

    async def test_worker_package_entry_consumes_isolated_fixture_job(self):
        import subprocess
        import sys
        self.cc.execute("SELECT COUNT(*) AS n FROM association_index_progress WHERE category_code IN ('PubNewProdTypeCode_00','PubNewProdTypeCode_01','PubNewProdTypeCode_02','PubNewProdTypeCode_03','PubNewProdTypeCode_04')")
        if self.cc.fetchone()['n']:
            self.skipTest('测试库存在正式类别进度，避免触发自动复查')
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        env = dict(os.environ, DATABASE_URL=test_database_url(), CRAWL_DATABASE_URL=test_crawl_url(), REDIS_URL=test_redis_url(), CRAWL_QUEUE_NAME=queue_name, ASSOCIATION_SOURCE='fixture')
        process = None
        try:
            process = await asyncio.create_subprocess_exec('node', '--env-file-if-exists=.env', 'scripts/python-service.mjs', 'worker', cwd=str(Path(__file__).resolve().parents[3]), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            await queue.add('association_index', {'taskId': self.task_id})
            async def wait_done():
                while True:
                    if process.returncode is not None: raise AssertionError('Worker 进程提前退出')
                    task = await asyncio.to_thread(self.tasks.get, self.task_id)
                    if task['status'] in ('completed', 'failed', 'partial_failed', 'cancelled'): return task
                    await asyncio.sleep(.05)
            task = await asyncio.wait_for(wait_done(), 15)
            self.assertEqual((task['status'], task['readCount'], task['reportedTotal']), ('completed', 0, 0))
        finally:
            if process is not None and process.returncode is None:
                if os.name == 'nt':
                    killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    await killer.wait()
                else:
                    process.terminate()
                try: await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await queue.obliterate(force=True)
            await queue.close()

    async def test_process_one_recorded_page_updates_task_and_shared_progress(self):
        record = self.record('测试保险', 'R1')
        class Adapter:
            async def read_page(self, category, cursor=None):
                return {'products': [record], 'reportedTotal': 1}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount'], task['reportedTotal']), ('completed', 1, 1, 1))
        self.assertEqual(task['units'][0]['state'], 'completed')
        self.assertEqual(len(self.tasks.list_task_products(self.task_id)), 1)
        self.cc.execute('SELECT next_page, completed_at FROM association_index_progress WHERE category_code = %s', (self.marker,))
        progress = self.cc.fetchone()
        self.assertEqual(progress['next_page'], 1)
        self.assertIsNotNone(progress['completed_at'])
        self.pc.execute('SELECT name, association_record_id FROM product WHERE company_id = (SELECT id FROM company WHERE name = %s)', (self.marker,))
        self.assertEqual(self.pc.fetchone(), {'name': '测试保险', 'association_record_id': 'R1'})

    async def test_paginates_and_tracks_shared_progress_and_deduplication(self):
        first = self.record('产品一', 'R1')
        second = self.record('产品二', 'R2')
        class Adapter:
            def __init__(self): self.cursors = []
            async def read_page(self, category, cursor=None):
                self.cursors.append(cursor)
                return {'products': [first], 'reportedTotal': 3, 'nextCursor': '2'} if cursor is None else {'products': [first, second], 'reportedTotal': 3}
        adapter = Adapter()
        await IndexTaskProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(self.task_id)
        self.assertEqual(adapter.cursors, [None, '2'])
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount']), ('completed', 3, 2))
        self.assertEqual([unit['page'] for unit in task['units']], [1, 2])
        self.assertEqual(len(self.tasks.list_task_products(self.task_id)), 2)
        self.cc.execute('SELECT next_page, completed_at FROM association_index_progress WHERE category_code = %s', (self.marker,))
        progress = self.cc.fetchone()
        self.assertEqual(progress['next_page'], 2)
        self.assertIsNotNone(progress['completed_at'])

    async def test_missing_later_page_total_preserves_previous_total(self):
        record = self.record('测试保险', 'R1')
        class Adapter:
            async def read_page(self, category, cursor=None):
                if cursor is None:
                    return {'products': [record], 'reportedTotal': 42, 'nextCursor': '2'}
                return {'products': [], 'reportedTotal': None}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['reportedTotal']), ('completed', 42))

    async def test_max_pages_stops_and_preserves_next_shared_cursor(self):
        self.scope['maxPages'] = 1
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        record = self.record('测试保险', 'R1')
        class Adapter:
            def __init__(self): self.calls = 0
            async def read_page(self, category, cursor=None):
                self.calls += 1
                if self.calls > 1: raise AssertionError('超出页面上限')
                return {'products': [record], 'reportedTotal': 20, 'nextCursor': '2'}
        adapter = Adapter()
        await IndexTaskProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(self.task_id)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.tasks.get(self.task_id)['status'], 'completed')
        self.cc.execute('SELECT next_page, \x60cursor\x60, completed_at FROM association_index_progress WHERE category_code=%s', (self.marker,))
        progress = self.cc.fetchone()
        self.assertEqual((progress['next_page'], progress['cursor'], progress['completed_at']), (2, '2', None))

    async def test_limited_task_resume_keeps_cap_and_next_task_preserves_product_snapshot(self):
        self.scope['maxPages'] = 1
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        first_record = self.record('限页产品', 'LIMIT-1')
        class FirstPage:
            async def read_page(self, category, cursor=None):
                return {'products': [first_record], 'reportedTotal': 2, 'nextCursor': '2'}
        await IndexTaskProcessor(test_crawl_url(), FirstPage(), self.ingestion, self.review).process(self.task_id)
        first_task_products = self.tasks.list_task_products(self.task_id)
        self.cc.execute("UPDATE crawl_task SET status='failed',finished_at=NULL WHERE id=%s", (self.task_id,))
        self.tasks._reset_task(self.task_id, ('completed',))
        class ResumeGuard:
            def __init__(self): self.calls = 0
            async def read_page(self, category, cursor=None):
                self.calls += 1
                raise AssertionError('续跑不得超过原任务 maxPages')
        resume_guard = ResumeGuard()
        await IndexTaskProcessor(test_crawl_url(), resume_guard, self.ingestion, self.review).process(self.task_id)
        self.assertEqual(resume_guard.calls, 0)
        self.assertEqual((self.tasks.get(self.task_id)['readCount'], len([unit for unit in self.tasks.get(self.task_id)['units'] if unit['state'] == 'completed'])), (1, 1))
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps({'category': self.category}), json.dumps({'retryAttempts': 1, 'requestTimeoutMs': 3000})))
        next_task_id = self.cc.lastrowid
        try:
            self.cc.execute('INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,\'pending\')', (next_task_id, self.marker + ':1'))
            second_record = self.record('续采产品', 'LIMIT-2')
            class NextPage:
                def __init__(self): self.cursors = []
                async def read_page(self, category, cursor=None):
                    self.cursors.append(cursor)
                    return {'products': [second_record], 'reportedTotal': 2}
            adapter = NextPage()
            await IndexTaskProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(next_task_id)
            self.assertEqual(adapter.cursors, ['2'])
            self.assertEqual(self.tasks.list_task_products(self.task_id), first_task_products)
            next_task_products = self.tasks.list_task_products(next_task_id)
            self.assertEqual(len(first_task_products), 1)
            self.assertEqual(len(next_task_products), 1)
            self.assertNotEqual(first_task_products, next_task_products)
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (next_task_id,))
            self.cc.execute('DELETE FROM crawl_task_product WHERE task_id=%s', (next_task_id,))
            self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (next_task_id,))
            self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (next_task_id,))

    async def test_sales_cutoff_keeps_boundary_page_for_later_resume(self):
        self.scope['saleAfter'] = '2026-01-01'
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        newer = {**self.record('较新', 'R1'), 'saleDateRaw': '2026-01-02'}
        boundary = {**self.record('边界', 'R2'), 'saleDateRaw': '2026-01-01'}
        class Adapter:
            async def read_page(self, category, cursor=None):
                if cursor is not None: raise AssertionError('越过日期边界')
                return {'products': [newer, boundary], 'reportedTotal': 20, 'nextCursor': '2'}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount']), ('completed', 2, 1))
        self.assertEqual(task['scope']['saleDateStopReason'], 'cutoff')
        self.cc.execute('SELECT next_page, \x60cursor\x60, completed_at FROM association_index_progress WHERE category_code=%s', (self.marker,))
        progress = self.cc.fetchone()
        self.assertEqual((progress['next_page'], progress['cursor'], progress['completed_at']), (1, None, None))

    async def test_unbounded_task_replays_boundary_page_after_date_cutoff(self):
        self.scope['saleAfter'] = '2026-01-01'
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        newer = {**self.record('较新', 'R1'), 'saleDateRaw': '2026-01-02'}
        boundary = {**self.record('边界', 'R2'), 'saleDateRaw': '2026-01-01'}
        class Adapter:
            async def read_page(self, category, cursor=None):
                return {'products': [newer, boundary], 'reportedTotal': 2}
        processor = IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        await processor.process(self.task_id)
        self.assertEqual((self.tasks.get(self.task_id)['readCount'], self.tasks.get(self.task_id)['uniqueCount']), (2, 1))
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps({'category': self.category}), json.dumps({'retryAttempts': 1, 'requestTimeoutMs': 3000})))
        second_id = self.cc.lastrowid
        try:
            self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (second_id, self.marker + ':1'))
            await processor.process(second_id)
            self.assertEqual((self.tasks.get(second_id)['status'], self.tasks.get(second_id)['readCount']), ('completed', 2))
            self.pc.execute('SELECT name FROM product WHERE company_id=(SELECT id FROM company WHERE name=%s)', (self.marker,))
            self.assertEqual({row['name'] for row in self.pc.fetchall()}, {'较新', '边界'})
        finally:
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task_product WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (second_id,))

    async def test_transient_source_failure_retries_then_processes(self):
        self.cc.execute('UPDATE crawl_task SET config=%s WHERE id=%s', (json.dumps({'retryAttempts': 2, 'requestTimeoutMs': 3000}), self.task_id))
        record = self.record('测试保险', 'R1')
        class Adapter:
            def __init__(self): self.calls = 0
            async def read_page(self, category, cursor=None):
                self.calls += 1
                if self.calls == 1: raise OSError('temporary failure')
                return {'products': [record], 'reportedTotal': 1}
        adapter = Adapter()
        await IndexTaskProcessor(test_crawl_url(), adapter, self.ingestion, self.review).process(self.task_id)
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(self.tasks.get(self.task_id)['status'], 'completed')

    async def test_invalid_sales_date_fails_without_advancing_progress_or_writing_products(self):
        self.scope['saleAfter'] = '2026-01-01'
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        record = {**self.record('未知日期', 'R1'), 'saleDateRaw': 'not-a-date'}
        class Adapter:
            async def read_page(self, category, cursor=None):
                return {'products': [record], 'reportedTotal': 1}
        with self.assertRaisesRegex(ValueError, '销售时间缺失或无效'):
            await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['units'][0]['state'], 'failed')
        self.assertEqual(self.tasks.list_task_products(self.task_id), [])
        self.cc.execute('SELECT * FROM association_index_progress WHERE category_code=%s', (self.marker,))
        self.assertIsNone(self.cc.fetchone())
        self.pc.execute('SELECT id FROM company WHERE name=%s', (self.marker,))
        self.assertIsNone(self.pc.fetchone())

    async def test_cancelled_during_fetch_does_not_write_page(self):
        tasks, task_id = self.tasks, self.task_id
        record = self.record('取消时产品', 'R1')
        class Adapter:
            async def read_page(self, category, cursor=None):
                tasks.cancel(task_id)
                return {'products': [record], 'reportedTotal': 1}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)
        self.assertEqual(self.tasks.get(self.task_id)['status'], 'cancelled')
        self.assertEqual(self.tasks.list_task_products(self.task_id), [])
        self.pc.execute('SELECT id FROM company WHERE name=%s', (self.marker,))
        self.assertIsNone(self.pc.fetchone())

    async def test_completed_category_before_refresh_is_skipped(self):
        self.cc.execute('INSERT INTO association_index_progress (category_code, category_name, next_page, cycle, completed_at) VALUES (%s,%s,9,2,UTC_TIMESTAMP())', (self.marker, '测试类别'))
        class Adapter:
            async def read_page(self, category, cursor=None):
                raise AssertionError('未到复查周期不应读取')
        class Settings:
            def get(self): return {'indexRefreshMinutes': 10080}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review, settings=Settings()).process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(task['scope']['indexSkipReason'], 'not_due')
        self.assertEqual(task['units'], [])
        self.cc.execute('SELECT next_page, cycle FROM association_index_progress WHERE category_code=%s', (self.marker,))
        self.assertEqual(self.cc.fetchone(), {'next_page': 9, 'cycle': 2})

    async def test_completed_category_after_refresh_starts_new_cycle(self):
        self.cc.execute('INSERT INTO association_index_progress (category_code, category_name, next_page, cycle, completed_at) VALUES (%s,%s,9,2,DATE_SUB(UTC_TIMESTAMP(), INTERVAL 8 DAY))', (self.marker, '测试类别'))
        calls = []
        class Adapter:
            async def read_page(self, category, cursor=None):
                calls.append(cursor)
                return {'products': [], 'reportedTotal': 0}
        class Settings:
            def get(self): return {'indexRefreshMinutes': 10080}
        await IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review, settings=Settings()).process(self.task_id)
        self.assertEqual(calls, [None])
        self.cc.execute('SELECT next_page, cycle FROM association_index_progress WHERE category_code=%s', (self.marker,))
        self.assertEqual(self.cc.fetchone(), {'next_page': 1, 'cycle': 3})

    async def test_concurrent_tasks_share_category_progress_without_blocking_event_loop(self):
        self.scope['maxPages'] = 1
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps(self.scope), json.dumps({'retryAttempts': 1, 'requestTimeoutMs': 3000})))
        second_id = self.cc.lastrowid
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (second_id, self.marker + ':1'))
        record_one, record_two = self.record('第一', 'R1'), self.record('第二', 'R2')
        calls = []
        class Adapter:
            async def read_page(self, category, cursor=None):
                calls.append(cursor)
                await asyncio.sleep(.2)
                return {'products': [record_one], 'reportedTotal': 2, 'nextCursor': '2'} if cursor is None else {'products': [record_two], 'reportedTotal': 2}
        class Processor(IndexTaskProcessor):
            def _connect(self):
                conn = super()._connect()
                with conn.cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 2')
                return conn
        processor = Processor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        running = [asyncio.create_task(processor.process(self.task_id)), asyncio.create_task(processor.process(second_id))]
        try:
            await asyncio.wait_for(asyncio.gather(*running), timeout=8)
            self.assertEqual(calls, [None, '2'])
            self.assertEqual(self.tasks.get(self.task_id)['status'], 'completed')
            self.assertEqual(self.tasks.get(second_id)['status'], 'completed')
        finally:
            await asyncio.gather(*running, return_exceptions=True)
            self.cc.execute('DELETE FROM crawl_task_product WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM review_item WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (second_id,))

    async def test_failure_after_first_page_preserves_progress_and_retry_resumes(self):
        first, second = self.record('第一', 'R1'), self.record('第二', 'R2')
        class Adapter:
            def __init__(self): self.fail = True; self.cursors = []
            async def read_page(self, category, cursor=None):
                self.cursors.append(cursor)
                if cursor is None:
                    return {'products': [first], 'reportedTotal': 2, 'nextCursor': '2'}
                if self.fail: raise OSError('source unavailable')
                return {'products': [second], 'reportedTotal': 2}
        adapter = Adapter()
        processor = IndexTaskProcessor(test_crawl_url(), adapter, self.ingestion, self.review)
        with self.assertRaises(OSError):
            await processor.process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount']), ('partial_failed', 1, 1))
        self.assertEqual([unit['state'] for unit in task['units']], ['completed', 'failed'])
        self.cc.execute('SELECT next_page, \x60cursor\x60 FROM association_index_progress WHERE category_code=%s', (self.marker,))
        self.assertEqual(self.cc.fetchone(), {'next_page': 2, 'cursor': '2'})
        self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s AND page=2', (self.task_id,))
        class Queue:
            async def enqueue(self, task_id): pass
        await MySQLTaskService(test_crawl_url(), queue=Queue()).retry(self.task_id)
        adapter.fail = False
        await processor.process(self.task_id)
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount']), ('completed', 2, 2))
        self.assertEqual(adapter.cursors, [None, '2', '2'])
        self.assertEqual(len(self.tasks.list_task_products(self.task_id)), 2)

    async def test_cancellation_while_waiting_for_shared_lock_skips_source_request(self):
        self.scope['maxPages'] = 1
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        self.cc.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps(self.scope), json.dumps({'retryAttempts': 1, 'requestTimeoutMs': 3000})))
        second_id = self.cc.lastrowid
        self.cc.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", (second_id, self.marker + ':1'))
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Adapter:
            async def read_page(self, category, cursor=None):
                calls.append(cursor)
                if cursor is None:
                    entered.set()
                    await release.wait()
                    return {'products': [], 'reportedTotal': 2, 'nextCursor': '2'}
                raise AssertionError('取消后不得请求下一页')
        processor = IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        first = asyncio.create_task(processor.process(self.task_id))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            second = asyncio.create_task(processor.process(second_id))
            await asyncio.sleep(.2)
            self.tasks.cancel(second_id)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), timeout=8)
            self.assertEqual(calls, [None])
            self.assertEqual(self.tasks.get(second_id)['status'], 'cancelled')
        finally:
            release.set()
            await asyncio.gather(first, *( [second] if second else [] ), return_exceptions=True)
            self.cc.execute('DELETE FROM crawl_task_product WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (second_id,))
            self.cc.execute('DELETE FROM crawl_task WHERE id=%s', (second_id,))

    async def test_recorded_encrypted_page_flows_through_adapter_and_processor(self):
        self.scope['maxPages'] = 1
        self.cc.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(self.scope), self.task_id))
        fixture = Path(__file__).resolve().parent / 'fixtures/association/list-page-1.json'
        recorded = json.loads(fixture.read_text(encoding='utf-8'))
        class Ingestion:
            def __init__(self): self.records = []
            def upsert(self, record, category, task_id):
                self.records.append(record)
                return {'status': 'inserted', 'productId': 900000000000 + len(self.records)}
        ingestion = Ingestion()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=recorded))) as client:
            adapter = AssociationAdapter(AssociationGateway(client, authorization_factory=lambda: 'fixed'))
            await IndexTaskProcessor(test_crawl_url(), adapter, ingestion, self.review).process(self.task_id)
        self.assertEqual(len(ingestion.records), 10)
        self.assertEqual(ingestion.records[0]['sourceRecordId'], 'ef5a99a2-4027-4b24-b9ef-009e99cc6235')
        self.assertEqual(ingestion.records[0]['salesStatus'], 'on_sale')
        task = self.tasks.get(self.task_id)
        self.assertEqual((task['status'], task['readCount'], task['uniqueCount'], task['reportedTotal']), ('completed', 10, 10, 14664))
        self.cc.execute('SELECT next_page, \x60cursor\x60 FROM association_index_progress WHERE category_code=%s', (self.marker,))
        self.assertEqual(self.cc.fetchone(), {'next_page': 2, 'cursor': '2'})

    async def test_node_produced_job_is_consumed_by_python_index_worker(self):
        queue_name = 'python-tdd-' + uuid4().hex
        redis_url = test_redis_url()
        record = self.record('队列产品', 'queue-id')
        class Adapter:
            async def read_page(self, category, cursor=None):
                return {'products': [record], 'reportedTotal': 1}
        processor = IndexTaskProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review)
        queue = Queue(queue_name, {'connection': redis_url})
        worker = create_index_worker(queue_name, redis_url, processor)
        try:
            code = '''const { Queue } = require('bullmq'); const { Redis } = require('ioredis'); (async () => {const c = new Redis(process.env.BAOXIAN_TEST_REDIS_URL, {maxRetriesPerRequest:null}); const q = new Queue(process.argv[1], {connection:c}); await q.add('association_index', {taskId:Number(process.argv[2])}, {jobId:'crawl-'+process.argv[2]}); await q.close(); c.disconnect();})().catch(e => {console.error(e.message);process.exit(1)});'''
            env = dict(os.environ, BAOXIAN_TEST_REDIS_URL=redis_url)
            process = await asyncio.create_subprocess_exec('node', '-e', code, queue_name, str(self.task_id), cwd=str(Path(__file__).resolve().parents[3]), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            self.assertEqual(process.returncode, 0, stderr.decode())
            async def wait_until_done():
                while True:
                    current = self.tasks.get(self.task_id)
                    if current['status'] in ('completed', 'failed', 'partial_failed'):
                        return current
                    await asyncio.sleep(.05)
            task = await asyncio.wait_for(wait_until_done(), timeout=8)
            self.assertEqual((task['status'], task['readCount']), ('completed', 1))
            self.assertEqual(len(self.tasks.list_task_products(self.task_id)), 1)
        finally:
            await worker.close()
            if queue_name.startswith('python-tdd-'):
                await queue.obliterate(force=True)
            await queue.close()
