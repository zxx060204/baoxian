import asyncio
import io

from minio import Minio
from minio.error import S3Error


class MinioStorage:
    def __init__(self, endpoint, port, use_ssl, access_key, secret_key, bucket):
        self.client = Minio(f'{endpoint}:{port}', access_key=access_key, secret_key=secret_key, secure=use_ssl)
        self.bucket = bucket
        self._ready = False
        self._lock = asyncio.Lock()

    async def _ensure_bucket(self):
        async with self._lock:
            if not self._ready:
                if not await asyncio.to_thread(self.client.bucket_exists, self.bucket):
                    await asyncio.to_thread(self.client.make_bucket, self.bucket)
                self._ready = True

    async def put(self, key, body, content_type):
        await self._ensure_bucket()
        await asyncio.to_thread(self.client.put_object, self.bucket, key, io.BytesIO(body), len(body), content_type=content_type)

    async def get(self, key):
        await self._ensure_bucket()
        def read():
            response = self.client.get_object(self.bucket, key)
            try:
                body = response.read()
            finally:
                response.close()
                response.release_conn()
            stat = self.client.stat_object(self.bucket, key)
            return {'body': body, 'contentType': stat.content_type or 'application/octet-stream'}
        return await asyncio.to_thread(read)

    async def exists(self, key):
        await self._ensure_bucket()
        try:
            await asyncio.to_thread(self.client.stat_object, self.bucket, key)
        except S3Error as error:
            if error.code == 'NoSuchKey':
                return False
            raise
        return True
