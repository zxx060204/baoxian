import asyncio
import time


ACQUIRE_SCRIPT = '''
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local last = redis.call('GET', key)
if last then
  local elapsed = now - tonumber(last)
  if elapsed < interval then return interval - elapsed end
end
redis.call('SET', key, now, 'PX', interval)
return 0
'''


class RedisDomainRateLimiter:
    def __init__(self, client):
        self.client = client

    async def wait(self, host, interval_ms):
        while True:
            delay = int(await self.client.eval(ACQUIRE_SCRIPT, 1, 'rate:domain:' + host, int(time.time() * 1000), max(0, interval_ms)))
            if delay <= 0:
                return
            await asyncio.sleep(min(delay, 250) / 1000)


RELEASE_LOCK_SCRIPT = '''
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
'''
RENEW_LOCK_SCRIPT = '''
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
'''


class RedisCompanyLock:
    def __init__(self, client, ttl_ms=600000):
        self.client = client
        self.ttl_ms = ttl_ms

    async def acquire(self, company_id):
        import secrets
        key = f'lock:company:{company_id}'
        token = secrets.token_hex(16)
        while not await self.client.set(key, token, px=self.ttl_ms, nx=True):
            await asyncio.sleep(.1)

        async def renew():
            while True:
                await asyncio.sleep(self.ttl_ms / 3000)
                if not await self.client.eval(RENEW_LOCK_SCRIPT, 1, key, token, self.ttl_ms):
                    return

        renewal = asyncio.create_task(renew())
        async def release():
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            await self.client.eval(RELEASE_LOCK_SCRIPT, 1, key, token)
        return release
