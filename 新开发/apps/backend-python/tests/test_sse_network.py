import asyncio
import json
import socket
import unittest

import httpx
import uvicorn

from baoxian_api.app import create_app


class SseNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_stops_polling_and_reconnect_delivers_terminal_state(self):
        class Tasks:
            def __init__(self): self.status = 'running'; self.calls = 0
            def get(self, task_id):
                self.calls += 1
                return {'id': task_id, 'status': self.status, 'type': 'association_index'}
        tasks = Tasks()
        app = create_app(internal_api_token='token', task_service=tasks)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        running = asyncio.create_task(server.serve(sockets=[listener]))
        async def wait_started():
            while not server.started:
                if running.done(): await running
                await asyncio.sleep(.01)
        async def next_event(lines):
            block = []
            async for line in lines:
                if not line:
                    if block: return block
                else: block.append(line)
            return block
        try:
            await asyncio.wait_for(wait_started(), 5)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
                self.assertEqual((await client.get('/api/crawl-tasks/17/events')).status_code, 401)
                async with client.stream('GET', '/api/crawl-tasks/17/events', headers={'Authorization': 'Bearer token'}) as response:
                    self.assertEqual(response.status_code, 200)
                    lines = response.aiter_lines()
                    first = await asyncio.wait_for(next_event(lines), 3)
                    self.assertEqual(json.loads(first[1][6:])['status'], 'running')
                await asyncio.sleep(1.4)
                after_close = tasks.calls
                await asyncio.sleep(1.3)
                self.assertEqual(tasks.calls, after_close)
                async with client.stream('GET', '/api/crawl-tasks/17/events', headers={'Authorization': 'Bearer token'}) as response:
                    lines = response.aiter_lines()
                    first = await asyncio.wait_for(next_event(lines), 3)
                    self.assertEqual(json.loads(first[1][6:])['status'], 'running')
                    tasks.status = 'completed'
                    changed = await asyncio.wait_for(next_event(lines), 3)
                    done = await asyncio.wait_for(next_event(lines), 3)
                    self.assertEqual(json.loads(changed[1][6:])['status'], 'completed')
                    self.assertEqual((done[0], json.loads(done[1][6:])), ('event: done', {'status': 'completed'}))
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, 5)
            listener.close()
