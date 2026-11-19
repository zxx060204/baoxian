import asyncio

from .companies import MySQLCompanyService


class IndexRefreshScheduler(MySQLCompanyService):
    def __init__(self, database_url, settings, tasks, categories=None):
        super().__init__(database_url)
        self.settings, self.tasks = settings, tasks
        names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
        self.categories = categories if categories is not None else {f'PubNewProdTypeCode_{index:02d}': name for index, name in enumerate(names)}

    def _lock_due_category(self, code, minutes):
        connection = self._connect()
        try:
            connection.begin()
            with connection.cursor() as cursor:
                cursor.execute('SELECT completed_at, completed_at <= UTC_TIMESTAMP() - INTERVAL %s MINUTE AS due FROM association_index_progress WHERE category_code=%s FOR UPDATE', (minutes, code))
                row = cursor.fetchone()
                if not row or not row['completed_at'] or not row['due']:
                    connection.rollback()
                    connection.close()
                    return None
                cursor.execute("SELECT id FROM crawl_task WHERE type='association_index' AND status IN ('queued','running') AND JSON_UNQUOTE(JSON_EXTRACT(scope, '$.category.code'))=%s LIMIT 1", (code,))
                if cursor.fetchone():
                    connection.rollback()
                    connection.close()
                    return None
            return connection
        except BaseException:
            connection.rollback()
            connection.close()
            raise

    async def check(self):
        config = await asyncio.to_thread(self.settings.get)
        created = []
        if not config['associationCrawlEnabled']:
            return {'created': created}
        for code, name in self.categories.items():
            connection = await asyncio.to_thread(self._lock_due_category, code, config['indexRefreshMinutes'])
            if connection is None:
                continue
            try:
                task = await self.tasks.create_index_task({'code': code, 'name': name})
                created.append(task['id'])
                await asyncio.to_thread(connection.commit)
            except BaseException:
                await asyncio.to_thread(connection.rollback)
                raise
            finally:
                await asyncio.to_thread(connection.close)
        return {'created': created}
