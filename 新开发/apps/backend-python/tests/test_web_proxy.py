import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from uuid import uuid4

import httpx
from bullmq import Queue
from test_company_api import test_database_url
from test_product_api import test_crawl_url
from test_queue import test_redis_url


class WebProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_vite_proxies_to_isolated_python_api(self):
        sockets = [socket.socket(), socket.socket()]
        try:
            for sock in sockets: sock.bind(('127.0.0.1', 0))
            api_port, web_port = [sock.getsockname()[1] for sock in sockets]
        finally:
            for sock in sockets: sock.close()
        root = Path(__file__).resolve().parents[3]
        backend = Path(__file__).resolve().parents[1]
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        env = dict(os.environ, INTERNAL_API_TOKEN='test-token', DATABASE_URL=test_database_url(), CRAWL_DATABASE_URL=test_crawl_url(), REDIS_URL=test_redis_url(), CRAWL_QUEUE_NAME=queue_name, API_PROXY_TARGET=f'http://127.0.0.1:{api_port}')
        processes = []
        task_id = None
        async def start(*args, cwd):
            process = await asyncio.create_subprocess_exec(*args, cwd=str(cwd), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            processes.append(process)
            return process
        try:
            api = await start(sys.executable, '-m', 'uvicorn', 'baoxian_api.main:create_application', '--factory', '--host', '127.0.0.1', '--port', str(api_port), '--log-level', 'error', cwd=backend)
            web = await start('node', str(root / 'node_modules' / 'vite' / 'bin' / 'vite.js'), 'apps/web', '--host', '127.0.0.1', '--port', str(web_port), '--strictPort', cwd=root)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{web_port}', timeout=2) as client:
                async def wait_ready():
                    for _ in range(150):
                        if api.returncode is not None or web.returncode is not None: raise AssertionError('API 或 Web 进程提前退出')
                        try:
                            response = await client.get('/api/system', headers={'Authorization': 'Bearer test-token'})
                            if response.status_code == 200 and 'python' in response.json(): return response
                        except (httpx.RequestError, ValueError): pass
                        await asyncio.sleep(.1)
                    raise AssertionError('Vite 未代理到 Python API')
                system = await asyncio.wait_for(wait_ready(), 25)
                self.assertTrue(system.json()['python'])
                self.assertEqual((await client.get('/api/system')).status_code, 401)
                self.assertEqual((await client.get('/health')).json()['status'], 'ok')
                page = await client.get('/')
                self.assertEqual(page.status_code, 200)
                self.assertIn('src/main.tsx', page.text)
                from baoxian_api.tasks import MySQLTaskService
                task_service = MySQLTaskService(test_crawl_url())
                marker = 'python-proxy-' + uuid4().hex
                with task_service._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("INSERT INTO crawl_task (type,status,scope) VALUES ('association_index','completed',%s)", (json.dumps({'category': {'code': marker, 'name': '测试'}}),))
                        task_id = cursor.lastrowid
                events = await client.get(f'/api/crawl-tasks/{task_id}/events', headers={'Authorization': 'Bearer test-token'})
                self.assertEqual(events.status_code, 200)
                self.assertIn('event: task', events.text)
                self.assertIn('event: done', events.text)
        finally:
            for process in reversed(processes):
                if process.returncode is None:
                    process.terminate()
                    try: await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
            if task_id is not None:
                from baoxian_api.tasks import MySQLTaskService
                with MySQLTaskService(test_crawl_url())._connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('DELETE FROM crawl_task WHERE id=%s', (task_id,))
            await queue.obliterate(force=True)
            await queue.close()
