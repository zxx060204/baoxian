import asyncio
import signal
import unittest
from unittest.mock import patch
from baoxian_api.worker_runtime import WorkerRuntime, create_runtime, register_shutdown_signals


class WorkerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_signal_fallback_requests_graceful_close(self):
        stopped = asyncio.Event()
        class Loop:
            def add_signal_handler(self, *_args):
                raise NotImplementedError
            def call_soon_threadsafe(self, callback):
                callback()
        registered = {}
        with patch('baoxian_api.worker_runtime.signal.signal', side_effect=lambda signum, callback: registered.__setitem__(signum, callback)):
            register_shutdown_signals(Loop(), stopped)
        self.assertIn(signal.SIGTERM, registered)
        registered[signal.SIGTERM](signal.SIGTERM, None)
        self.assertTrue(stopped.is_set())

    async def test_close_releases_remaining_resources_when_queue_close_fails(self):
        events = []
        class Worker:
            async def close(self): events.append('worker')
        class Queue:
            async def close(self): events.append('queue'); raise RuntimeError('队列关闭失败')
        class Redis:
            async def aclose(self): events.append('redis')
        class Client:
            async def aclose(self): events.append('client')
        runtime = WorkerRuntime(Worker(), Redis(), Client(), True, Queue())
        with self.assertRaisesRegex(RuntimeError, '队列关闭失败'):
            await runtime.close()
        self.assertEqual(events, ['worker', 'queue', 'redis', 'client'])

    async def test_startup_failure_releases_all_resources_when_queue_close_fails(self):
        events = []
        class Settings:
            def __init__(self, url): pass
            def get(self): return {'globalConcurrency': 1}
        class Queue:
            def __init__(self, *args): self.queue = self
            async def upsertJobScheduler(self, *args, **kwargs): raise RuntimeError('调度器注册失败')
            async def close(self): events.append('queue'); raise RuntimeError('队列关闭失败')
        class Redis:
            async def aclose(self): events.append('redis')
        class Client:
            async def aclose(self): events.append('client')
        env = {'DATABASE_URL': 'mysql://unused/product', 'CRAWL_DATABASE_URL': 'mysql://unused/crawl', 'REDIS_URL': 'redis://unused'}
        with patch('baoxian_api.worker_runtime.MySQLSettingsService', Settings), \
             patch('baoxian_api.worker_runtime.BullMQTaskQueue', Queue), \
             patch('baoxian_api.worker_runtime.redis.from_url', return_value=Redis()), \
             patch('baoxian_api.worker_runtime.httpx.AsyncClient', return_value=Client()):
            with self.assertRaisesRegex(RuntimeError, '调度器注册失败'):
                await create_runtime(env, storage=object())
        self.assertEqual(events, ['queue', 'redis', 'client'])
