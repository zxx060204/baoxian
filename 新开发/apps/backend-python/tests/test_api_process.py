import asyncio
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


class ApiProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_package_entry_runs_with_isolated_services(self):
        queue_name = 'python-tdd-' + uuid4().hex
        queue = Queue(queue_name, {'connection': test_redis_url()})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, INTERNAL_API_TOKEN='test-token', DATABASE_URL=test_database_url(), CRAWL_DATABASE_URL=test_crawl_url(), REDIS_URL=test_redis_url(), CRAWL_QUEUE_NAME=queue_name, API_HOST='127.0.0.1', API_PORT=str(port))
        process = None
        try:
            process = await asyncio.create_subprocess_exec('node', '--env-file-if-exists=.env', 'scripts/python-service.mjs', 'api', cwd=str(Path(__file__).resolve().parents[3]), env=env, stdout=subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=2) as client:
                async def wait_ready():
                    for _ in range(100):
                        if process.returncode is not None: raise AssertionError('API 进程提前退出: ' + (await process.stderr.read()).decode(errors='replace'))
                        try:
                            response = await client.get('/health')
                            if response.status_code == 200: return response
                        except httpx.RequestError: pass
                        await asyncio.sleep(.1)
                    raise AssertionError('API 未在限定时间内启动')
                health = await asyncio.wait_for(wait_ready(), 15)
                self.assertEqual(health.json()['status'], 'ok')
                self.assertEqual((await client.get('/api/system')).status_code, 401)
                headers = {'Authorization': 'Bearer test-token'}
                system = await client.get('/api/system', headers=headers)
                self.assertTrue(system.json()['python'])
                products = await client.get('/api/products', headers=headers, params={'limit': 1})
                self.assertEqual(products.status_code, 200)
                self.assertIsInstance(products.json()['items'], list)
                schedules = await client.get('/api/schedules', headers=headers)
                self.assertEqual(schedules.status_code, 200)
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
