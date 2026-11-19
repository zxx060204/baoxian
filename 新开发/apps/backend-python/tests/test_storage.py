import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

from baoxian_api.storage import MinioStorage


def minio_settings():
    env = {}
    path = Path(__file__).resolve().parents[3] / '.env'
    for line in path.read_text(encoding='utf-8').splitlines():
        key, sep, value = line.partition('=')
        if sep: env[key.strip()] = value.strip().strip('\"\'')
    env.update({key: value for key, value in os.environ.items() if key.startswith('MINIO_')})
    return env


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolated_bucket_preserves_bytes_and_content_type(self):
        env = minio_settings()
        bucket = 'python-tdd-' + uuid4().hex
        storage = MinioStorage(env.get('MINIO_ENDPOINT', '127.0.0.1'), int(env.get('MINIO_PORT', '9000')), env.get('MINIO_USE_SSL') == 'true', env['MINIO_ACCESS_KEY'], env['MINIO_SECRET_KEY'], bucket)
        key = 'documents/test.pdf'
        try:
            await storage.put(key, b'%PDF-example', 'application/pdf')
            result = await storage.get(key)
            self.assertEqual(result, {'body': b'%PDF-example', 'contentType': 'application/pdf'})
        finally:
            if bucket.startswith('python-tdd-'):
                def cleanup():
                    if storage.client.bucket_exists(bucket):
                        storage.client.remove_object(bucket, key)
                        storage.client.remove_bucket(bucket)
                await asyncio.to_thread(cleanup)
