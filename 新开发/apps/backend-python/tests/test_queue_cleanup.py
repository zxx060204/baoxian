import unittest
from unittest.mock import patch

from baoxian_api.queue import OnDemandBullMQTaskQueue


class QueueCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_operations_survive_close_failure(self):
        operations = []
        class Queue:
            def __init__(self, *args): pass
            async def enqueue(self, *args): operations.append('enqueue')
            async def upsert_schedule(self, *args): operations.append('upsert')
            async def remove_schedule(self, *args): operations.append('remove')
            async def close(self): raise RuntimeError('close failed')
        with patch('baoxian_api.queue.BullMQTaskQueue', Queue), self.assertLogs('baoxian_api.queue', level='ERROR'):
            queue = OnDemandBullMQTaskQueue('redis://unused', 'test')
            await queue.enqueue(1)
            await queue.upsert_schedule(1, 'code', 2)
            await queue.remove_schedule(1)
        self.assertEqual(operations, ['enqueue', 'upsert', 'remove'])

    async def test_enqueue_error_is_not_replaced_by_close_error(self):
        class Queue:
            def __init__(self, *args): pass
            async def enqueue(self, *args): raise RuntimeError('enqueue failed')
            async def close(self): raise RuntimeError('close failed')
        with patch('baoxian_api.queue.BullMQTaskQueue', Queue), self.assertLogs('baoxian_api.queue', level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'enqueue failed'):
                await OnDemandBullMQTaskQueue('redis://unused').enqueue(1)
