import asyncio
import pymysql

from .companies import MySQLCompanyService
from .tasks import public_row, QueueUnavailableError


class DuplicateScheduleError(Exception): pass
class ScheduleNotFoundError(Exception): pass


class MySQLScheduleService(MySQLCompanyService):
    def __init__(self, database_url, queue):
        super().__init__(database_url)
        self.queue = queue

    def _rows(self, sql, args=()):
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, args)
                rows = cursor.fetchall()
        result = [public_row(row) for row in rows]
        for row in result: row['enabled'] = bool(row['enabled'])
        return result

    async def list(self):
        return await asyncio.to_thread(self._rows, 'SELECT * FROM crawl_schedule ORDER BY id')

    def _create(self, category, every_minutes, enabled):
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('INSERT INTO crawl_schedule (category_code,category_name,every_minutes,enabled) VALUES (%s,%s,%s,%s)', (category['code'], category['name'], every_minutes, enabled))
                    return cursor.lastrowid
        except pymysql.IntegrityError as error:
            if error.args[0] == 1062: raise DuplicateScheduleError(category['code']) from error
            raise

    async def _get(self, schedule_id):
        rows = await asyncio.to_thread(self._rows, 'SELECT * FROM crawl_schedule WHERE id=%s', (schedule_id,))
        if not rows: raise ScheduleNotFoundError(schedule_id)
        return rows[0]

    async def get(self, schedule_id):
        return await self._get(schedule_id)

    def _lock_enabled(self, schedule_id, category_code):
        connection = self._connect()
        try:
            connection.begin()
            with connection.cursor() as cursor:
                cursor.execute('SELECT enabled,category_code FROM crawl_schedule WHERE id=%s FOR UPDATE', (schedule_id,))
                row = cursor.fetchone()
                if not row or not row['enabled'] or row['category_code'] != category_code:
                    connection.rollback()
                    connection.close()
                    return None
            return connection
        except BaseException:
            connection.rollback()
            connection.close()
            raise

    async def run_if_enabled(self, schedule_id, category_code, operation):
        connection = await asyncio.to_thread(self._lock_enabled, schedule_id, category_code)
        if connection is None:
            return None
        try:
            result = await operation()
            await asyncio.to_thread(connection.commit)
            return result
        except BaseException:
            await asyncio.to_thread(connection.rollback)
            raise
        finally:
            await asyncio.to_thread(connection.close)

    async def _sync(self, row):
        try:
            if row['enabled']:
                await self.queue.upsert_schedule(row['id'], row['categoryCode'], row['everyMinutes'])
            else:
                await self.queue.remove_schedule(row['id'])
        except Exception as error:
            raise QueueUnavailableError() from error

    async def create(self, category, every_minutes, enabled=True):
        schedule_id = await asyncio.to_thread(self._create, category, every_minutes, enabled)
        row = await self._get(schedule_id)
        if enabled: await self._sync(row)
        return row

    async def update(self, schedule_id, changes):
        await self._get(schedule_id)
        columns = {'everyMinutes': 'every_minutes', 'enabled': 'enabled'}
        def write():
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('UPDATE crawl_schedule SET ' + ', '.join(columns[key] + '=%s' for key in changes) + ', updated_at=UTC_TIMESTAMP() WHERE id=%s', (*changes.values(), schedule_id))
        await asyncio.to_thread(write)
        row = await self._get(schedule_id)
        await self._sync(row)
        return row

    async def sync_schedulers(self):
        for row in await self.list(): await self._sync(row)
