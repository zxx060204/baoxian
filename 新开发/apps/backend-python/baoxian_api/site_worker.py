import asyncio

from .companies import MySQLCompanyService
from .tasks import MySQLTaskService, TaskNotFoundError


class CompanySiteDiscoveryProcessor(MySQLCompanyService):
    def __init__(self, crawl_database_url, product_database_url, disclosure, review=None):
        super().__init__(crawl_database_url)
        self.tasks = MySQLTaskService(crawl_database_url)
        self.product = MySQLCompanyService(product_database_url)
        self.disclosure = disclosure
        self.review = review

    def _execute(self, statement, args):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, args)
                return cursor.rowcount

    def _update_site(self, company_id, url):
        with self.product._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT official_site_url FROM company WHERE id=%s', (company_id,))
                current = cursor.fetchone()
                if current and current['official_site_url']:
                    raise ValueError('公司已配置官网，不覆盖现有值')
                cursor.execute('UPDATE company SET official_site_url=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s AND official_site_url IS NULL', (url, company_id))
                if cursor.rowcount == 0:
                    raise ValueError('公司不存在或已配置官网，不覆盖现有值')

    async def process(self, task_id):
        task = await asyncio.to_thread(self.tasks.get, task_id)
        if task['status'] in ('completed', 'cancelled'):
            return
        changed = await asyncio.to_thread(self._execute, "UPDATE crawl_task SET status='running',started_at=COALESCE(started_at,UTC_TIMESTAMP()),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status NOT IN ('completed','cancelled')", (task_id,))
        if not changed:
            return
        completed = failed = 0
        for company in task['scope'].get('companies', []):
            try:
                latest = await asyncio.to_thread(self.tasks.get, task_id)
            except TaskNotFoundError:
                return
            if latest['status'] == 'cancelled':
                return
            unit_key = f"company-site:{company['id']}"
            try:
                lookup = asyncio.create_task(self.disclosure.find(company['name']))
                try:
                    while not lookup.done():
                        await asyncio.wait({lookup}, timeout=.5)
                        if not lookup.done():
                            latest = await asyncio.to_thread(self.tasks.get, task_id)
                            if latest['status'] == 'cancelled':
                                lookup.cancel()
                                await asyncio.gather(lookup, return_exceptions=True)
                                return
                    result = await lookup
                finally:
                    if not lookup.done():
                        lookup.cancel()
                        await asyncio.gather(lookup, return_exceptions=True)
                latest = await asyncio.to_thread(self.tasks.get, task_id)
                if latest['status'] == 'cancelled':
                    return
                if result['kind'] == 'unavailable':
                    if self.review is not None:
                        await asyncio.to_thread(self.review.record_company_site, task_id, company['id'], company['name'], [], result['reason'])
                    raise ValueError(result['reason'])
                await asyncio.to_thread(self._update_site, company['id'], result['url'])
                completed += 1
                await asyncio.to_thread(self._execute, "UPDATE crawl_task_unit SET state='completed',last_error=NULL,updated_at=UTC_TIMESTAMP() WHERE task_id=%s AND unit_key=%s", (task_id, unit_key))
            except Exception as error:
                failed += 1
                await asyncio.to_thread(self._execute, "UPDATE crawl_task_unit SET state='failed',last_error=%s,updated_at=UTC_TIMESTAMP() WHERE task_id=%s AND unit_key=%s", (str(error), task_id, unit_key))
        status = 'completed' if not failed else 'failed' if not completed else 'partial_failed'
        await asyncio.to_thread(self._execute, "UPDATE crawl_task SET status=%s,finished_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status<>'cancelled'", (status, task_id))
