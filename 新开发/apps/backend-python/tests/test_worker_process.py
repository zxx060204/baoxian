import asyncio
import json
import os
import signal
import subprocess
import sys
import unittest
import shutil
from pathlib import Path
from uuid import uuid4

from bullmq import Queue

from baoxian_api.tasks import MySQLTaskService
from baoxian_api.worker import create_worker
from test_queue import test_redis_url
from test_company_api import test_database_url
from test_product_api import test_crawl_url


class WorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue_name = 'python-worker-process-' + uuid4().hex
        self.redis_url = test_redis_url()
        self.queue = Queue(self.queue_name, {'connection': self.redis_url})
        self.backend_dir = Path(__file__).resolve().parents[1]
        self.env = dict(os.environ)
        self.env.update({
            'DATABASE_URL': test_database_url(),
            'CRAWL_DATABASE_URL': test_crawl_url(),
            'REDIS_URL': self.redis_url,
            'CRAWL_QUEUE_NAME': self.queue_name,
            'ASSOCIATION_SOURCE': 'fixture',
        })
        self.tasks = MySQLTaskService(test_crawl_url())
        self.task_id = None

    async def asyncTearDown(self):
        if self.task_id is not None:
            with self.tasks._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
                    cursor.execute('DELETE FROM crawl_task WHERE id=%s', (self.task_id,))
        await self.queue.obliterate(force=True)
        await self.queue.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows 控制台关闭信号验证')
    async def test_windows_control_break_closes_worker_runtime_process(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-u', '-m', 'baoxian_api.worker_runtime',
            cwd=str(self.backend_dir), env=self.env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        )
        try:
            async def wait_until_ready():
                for _ in range(100):
                    if process.returncode is not None:
                        error = await process.stderr.read()
                        raise AssertionError('Worker 提前退出：' + error.decode(errors='replace'))
                    schedulers = await self.queue.getJobSchedulers()
                    keys = {item['key'] for item in schedulers}
                    if {'association-refresh-check', 'task-state-recovery'} <= keys:
                        return
                    await asyncio.sleep(.1)
                raise AssertionError('Worker 未完成启动')
            await asyncio.wait_for(wait_until_ready(), 15)
            await asyncio.to_thread(os.kill, process.pid, signal.CTRL_BREAK_EVENT)
            await asyncio.wait_for(process.wait(), 10)
            self.assertEqual(process.returncode, 0, (await process.stderr.read()).decode(errors='replace'))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    @unittest.skipUnless(os.name == 'nt', 'Windows 控制台关闭信号验证')
    async def test_windows_control_break_closes_default_worker_launcher(self):
        node = shutil.which('node')
        self.assertIsNotNone(node)
        script = self.backend_dir.parents[1] / 'scripts' / 'python-service.mjs'
        process = await asyncio.create_subprocess_exec(
            node, str(script), 'worker', cwd=str(script.parent.parent), env=self.env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        )
        try:
            async def wait_until_ready():
                for _ in range(100):
                    if process.returncode is not None:
                        error = await process.stderr.read()
                        raise AssertionError('Worker 提前退出：' + error.decode(errors='replace'))
                    schedulers = await self.queue.getJobSchedulers()
                    keys = {item['key'] for item in schedulers}
                    if {'association-refresh-check', 'task-state-recovery'} <= keys:
                        return
                    await asyncio.sleep(.1)
                raise AssertionError('默认 Worker 启动器未完成启动')
            await asyncio.wait_for(wait_until_ready(), 15)
            await asyncio.to_thread(os.kill, process.pid, signal.CTRL_BREAK_EVENT)
            await asyncio.wait_for(process.wait(), 10)
            self.assertEqual(process.returncode, 0, (await process.stderr.read()).decode(errors='replace'))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_bullmq_reclaims_job_after_worker_process_is_hard_killed(self):
        scope = {'category': {'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'}}
        with self.tasks._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('association_index','queued',%s,%s)", (json.dumps(scope), '{}'))
                self.task_id = cursor.lastrowid
                cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,'PubNewProdTypeCode_00:1',1,'pending')", (self.task_id,))
        await self.queue.add('association_index', {'taskId': self.task_id}, {'jobId': f'crawl-{self.task_id}'})
        child = '''
import asyncio, os
from bullmq import Worker
from baoxian_api.tasks import MySQLTaskService

tasks = MySQLTaskService(os.environ['CRAWL_DATABASE_URL'])

def mark_running(task_id):
    with tasks._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_task SET status='running',started_at=UTC_TIMESTAMP() WHERE id=%s", (task_id,))
            cursor.execute("UPDATE crawl_task_unit SET state='running' WHERE task_id=%s", (task_id,))

async def hold(job, token):
    await asyncio.to_thread(mark_running, job.data['taskId'])
    print('claimed', flush=True)
    await asyncio.Event().wait()

async def main():
    Worker(os.environ['CRAWL_QUEUE_NAME'], hold, {
        'connection': os.environ['REDIS_URL'],
        'lockDuration': 800,
        'stalledInterval': 300,
        'maxStalledCount': 2,
    })
    await asyncio.Event().wait()

asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-u', '-c', child,
            cwd=str(self.backend_dir), env=self.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        worker = None
        try:
            claimed = await asyncio.wait_for(process.stdout.readline(), 15)
            self.assertEqual(claimed.strip(), b'claimed')
            self.assertEqual(self.tasks.get(self.task_id)['status'], 'running')
            process.kill()
            await asyncio.wait_for(process.wait(), 5)

            recovered = asyncio.Event()
            class CompleteRecoveredTask:
                async def process(inner_self, task_id):
                    def complete():
                        with self.tasks._connect() as connection:
                            with connection.cursor() as cursor:
                                cursor.execute("UPDATE crawl_task_unit SET state='completed' WHERE task_id=%s", (task_id,))
                                cursor.execute("UPDATE crawl_task SET status='completed',finished_at=UTC_TIMESTAMP() WHERE id=%s", (task_id,))
                    await asyncio.to_thread(complete)
                    recovered.set()
            worker = create_worker(self.queue_name, self.redis_url, self.tasks, {'association_index': CompleteRecoveredTask()}, worker_options={
                'lockDuration': 800,
                'stalledInterval': 300,
                'maxStalledCount': 2,
            })
            await asyncio.wait_for(recovered.wait(), 10)
            await worker.close()
            worker = None
            task = self.tasks.get(self.task_id)
            self.assertEqual((task['status'], task['units'][0]['state']), ('completed', 'completed'))
            job = await self.queue.getJob(f'crawl-{self.task_id}')
            self.assertEqual(await job.getState(), 'completed')
        finally:
            if worker is not None:
                await worker.close(force=True)
            if process.returncode is None:
                process.kill()
                await process.wait()


if __name__ == '__main__':
    unittest.main()
