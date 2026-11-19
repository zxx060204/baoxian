import asyncio
import json
import re
from datetime import datetime, timezone

from .companies import MySQLCompanyService
from .database_helpers import qualified_database
from .queue import ACTIVE_JOB_STATES, TERMINAL_JOB_STATES


class AssociationDisabledError(Exception):
    pass


class QueueUnavailableError(Exception):
    pass


class TaskNotControllableError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


def public_row(row):
    result = {}
    for key, value in row.items():
        pieces = key.split('_')
        field = pieces[0] + ''.join(part.capitalize() for part in pieces[1:])
        if isinstance(value, datetime):
            value = value.replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        elif key in ('scope', 'config') and isinstance(value, str):
            value = json.loads(value)
        if key == 'scope' and isinstance(value, dict):
            value = {name: item for name, item in value.items() if not name.startswith('_')}
        result[field] = value
    return result



def classify_failure(message):
    if not message: return 'unknown'
    text = message.lower()
    for kind, words in (('timeout', ('timeout', 'timed out', '超时')), ('cancelled', ('abort', '取消')), ('decrypt', ('decrypt', '解密')), ('http', ('http',)), ('invalid_response', ('json', '响应')), ('network', ('fetch', '网络', 'econn'))):
        if any(word in text for word in words): return kind
    return 'source'

class MySQLTaskService(MySQLCompanyService):
    def __init__(self, database_url, settings=None, queue=None, product_database_url=None):
        super().__init__(database_url)
        self.settings = settings
        self.queue = queue
        self.product = MySQLCompanyService(product_database_url) if product_database_url else None

    async def _enqueue_task(self, task_id):
        try:
            await self.queue.enqueue(task_id)
        except Exception as error:
            probe = getattr(self.queue, 'contains_task', None)
            uncertain = False
            if probe is not None:
                try:
                    if await probe(task_id):
                        return
                except Exception:
                    uncertain = True
            if not uncertain:
                def mark_failed_if_queued():
                    with self._connect() as connection:
                        with connection.cursor() as cursor:
                            cursor.execute("UPDATE crawl_task SET status='failed',updated_at=UTC_TIMESTAMP() WHERE id=%s AND status='queued'", (task_id,))
                await asyncio.to_thread(mark_failed_if_queued)
            raise QueueUnavailableError() from error

    def _list_recoverable_tasks(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,type,status,scope FROM crawl_task WHERE status IN ('queued','running') ORDER BY id")
                rows = cursor.fetchall()
        for row in rows:
            if isinstance(row['scope'], str):
                row['scope'] = json.loads(row['scope'])
        return rows

    def _mark_worker_interrupted(self, task_id, reason):
        product_database = qualified_database(self, self.product) if self.product else None
        with self._connect() as connection:
            try:
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT type,status,scope FROM crawl_task WHERE id=%s FOR UPDATE', (task_id,))
                    task = cursor.fetchone()
                    if task is None or task['status'] != 'running':
                        connection.rollback()
                        return False
                    scope = json.loads(task['scope']) if isinstance(task['scope'], str) else task['scope']
                    cursor.execute("UPDATE crawl_task_unit SET state='failed',last_error=%s,updated_at=UTC_TIMESTAMP() WHERE task_id=%s AND state IN ('pending','running')", (reason, task_id))
                    cursor.execute("UPDATE crawl_task SET status='failed',finished_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status='running'", (task_id,))
                    if product_database and task['type'] == 'company_documents':
                        product_ids = [item.get('id') for item in scope.get('products', []) if isinstance(item.get('id'), int)]
                        if product_ids:
                            placeholders = ','.join(['%s'] * len(product_ids))
                            cursor.execute(
                                f"UPDATE {product_database}.product SET integration_status='failed',integration_status_reason=%s,integration_last_task_id=%s,integration_status_updated_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id IN ({placeholders}) AND integration_status='running' AND integration_last_task_id=%s",
                                (reason, task_id, *product_ids, task_id),
                            )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    async def reconcile_worker_state(self):
        if self.queue is None or not hasattr(self.queue, 'get_job_state'):
            return {'reenqueued': 0, 'failed': 0, 'active': 0}
        tasks = await asyncio.to_thread(self._list_recoverable_tasks)
        stats = {'reenqueued': 0, 'failed': 0, 'active': 0}
        active_scheduled = set()
        get_active_scheduled = getattr(self.queue, 'active_scheduled_categories', None)
        if get_active_scheduled is not None:
            active_scheduled = await get_active_scheduled()
        reason = 'Worker 进程中断，BullMQ 中已无可恢复作业'
        for task in tasks:
            scope = task['scope'] if isinstance(task['scope'], dict) else {}
            job_id = scope.get('_queueJobId') or f"crawl-{task['id']}"
            state = await self.queue.get_job_state(job_id)
            if task['status'] == 'queued':
                if state is None or state in TERMINAL_JOB_STATES:
                    await self._enqueue_task(task['id'])
                    stats['reenqueued'] += 1
                else:
                    stats['active'] += 1
                continue
            if state in ACTIVE_JOB_STATES:
                stats['active'] += 1
                continue
            category = (scope.get('category') or {}).get('code')
            if task['type'] == 'association_index' and not scope.get('_queueJobId') and category in active_scheduled:
                stats['active'] += 1
                continue
            if state is None or state in TERMINAL_JOB_STATES:
                if await asyncio.to_thread(self._mark_worker_interrupted, task['id'], reason):
                    stats['failed'] += 1
        return stats

    async def create_index_task(self, category, max_pages=None, sale_after=None, *, enqueue=True, queue_job_id=None):
        settings = await asyncio.to_thread(self.settings.get)
        if not settings['associationCrawlEnabled']:
            raise AssociationDisabledError()
        scope = {'category': category}
        if queue_job_id is not None:
            scope['_queueJobId'] = str(queue_job_id)
        if max_pages is not None:
            scope['maxPages'] = max_pages
        if sale_after is not None:
            scope['saleAfter'] = sale_after
        config = {key: settings[key] for key in ('globalConcurrency', 'domainIntervalMs', 'requestTimeoutMs', 'retryAttempts', 'associationCrawlEnabled')}
        def create():
            with self._connect() as connection:
                try:
                    connection.begin()
                    with connection.cursor() as cursor:
                        cursor.execute("INSERT INTO crawl_task (type, status, scope, config) VALUES ('association_index','queued',%s,%s)", (json.dumps(scope, ensure_ascii=False), json.dumps(config)))
                        task_id = cursor.lastrowid
                        cursor.execute("INSERT INTO crawl_task_unit (task_id, unit_key, page, state) VALUES (%s,%s,1,'pending')", (task_id, category['code'] + ':1'))
                    connection.commit()
                    return task_id
                except Exception:
                    connection.rollback()
                    raise
        task_id = await asyncio.to_thread(create)
        if enqueue:
            await self._enqueue_task(task_id)
        return await asyncio.to_thread(self.get, task_id)

    def list(self, status=None, task_type=None):
        conditions = []
        args = []
        if status is not None:
            conditions.append('status = %s')
            args.append(status)
        if task_type is not None:
            conditions.append('type = %s')
            args.append(task_type)
        query = 'SELECT * FROM crawl_task'
        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query + ' ORDER BY id DESC', args)
                return [public_row(row) for row in cursor.fetchall()]

    def get(self, task_id):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM crawl_task WHERE id = %s', (task_id,))
                row = cursor.fetchone()
                if row is None:
                    raise TaskNotFoundError(task_id)
                cursor.execute('SELECT * FROM crawl_task_unit WHERE task_id = %s ORDER BY page', (task_id,))
                task = public_row(row)
                task['units'] = [public_row(unit) for unit in cursor.fetchall()]
                return task

    def index_progress(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM association_index_progress')
                return [public_row(row) for row in cursor.fetchall()]

    def coverage(self):
        result = []
        for item in self.list(task_type='association_index'):
            task = self.get(item['id'])
            units = task['units']
            pages = [unit['page'] for unit in units]
            failed = [unit for unit in units if unit['state'] == 'failed']
            category = task['scope']['category']
            result.append({'taskId': task['id'], 'categoryCode': category['code'], 'categoryName': category['name'], 'status': task['status'], 'pageFrom': min(pages) if pages else None, 'pageTo': max(pages) if pages else None, 'reportedTotal': task['reportedTotal'], 'readCount': task['readCount'], 'uniqueCount': task['uniqueCount'], 'duplicateCount': max(0, task['readCount'] - task['uniqueCount']), 'failedUnits': len(failed), 'failures': [{'page': unit['page'], 'error': unit['lastError'], 'kind': classify_failure(unit['lastError'])} for unit in failed]})
        return result

    def list_task_products(self, task_id):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT product_id FROM crawl_task_product WHERE task_id = %s', (task_id,))
                return [row['product_id'] for row in cursor.fetchall()]

    def cancel(self, task_id):
        product_database = qualified_database(self, self.product) if self.product else None
        with self._connect() as connection:
            try:
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE crawl_task SET status = 'cancelled', finished_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP() WHERE id = %s AND status NOT IN ('completed', 'cancelled')", (task_id,))
                    if cursor.rowcount == 0:
                        cursor.execute('SELECT status FROM crawl_task WHERE id = %s', (task_id,))
                        task = cursor.fetchone()
                        if task is None:
                            raise TaskNotFoundError(task_id)
                        raise TaskNotControllableError(f"任务 {task_id} 当前不可操作：状态为 {task['status']}")
                    if product_database:
                        cursor.execute('SELECT scope FROM crawl_task WHERE id=%s', (task_id,))
                        raw = cursor.fetchone()['scope']
                        scope = json.loads(raw) if isinstance(raw, str) else raw
                        for product_id, previous in scope.get('_integrationBefore', {}).items():
                            cursor.execute(f"UPDATE {product_database}.product SET integration_status=%s,integration_status_reason=%s,integration_last_task_id=%s,integration_status_updated_at=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s AND integration_status='running' AND integration_last_task_id=%s", (previous['integration_status'], previous['integration_status_reason'], previous['integration_last_task_id'], previous['integration_status_updated_at'], int(product_id), task_id))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(task_id)

    def _reset_task(self, task_id, forbidden_statuses):
        with self._connect() as connection:
            try:
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT status FROM crawl_task WHERE id = %s FOR UPDATE', (task_id,))
                    task = cursor.fetchone()
                    if task is None:
                        raise TaskNotFoundError(task_id)
                    if task['status'] in forbidden_statuses:
                        raise TaskNotControllableError(f"任务 {task_id} 当前不可操作：状态为 {task['status']}")
                    cursor.execute("UPDATE crawl_task_unit SET state = 'pending', last_error = NULL, updated_at = UTC_TIMESTAMP() WHERE task_id = %s AND state IN ('failed','running')", (task_id,))
                    cursor.execute("UPDATE crawl_task SET status = 'queued', started_at = NULL, finished_at = NULL, updated_at = UTC_TIMESTAMP() WHERE id = %s", (task_id,))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    async def retry(self, task_id):
        await asyncio.to_thread(self._reset_task, task_id, ('queued', 'running', 'completed'))
        await self._enqueue_task(task_id)
        return await asyncio.to_thread(self.get, task_id)

    async def create_company_task(self, company, products):
        settings = await asyncio.to_thread(self.settings.get)
        config = {key: settings[key] for key in ('globalConcurrency', 'domainIntervalMs', 'requestTimeoutMs', 'retryAttempts', 'associationCrawlEnabled')}
        task_products = []
        for product in products:
            confirmed_name = product.get('confirmedName')
            confirmed_status = product.get('confirmedSalesStatus')
            task_products.append({
                'id': product['id'],
                'name': confirmed_name if confirmed_name is not None else product['name'],
                'salesStatus': confirmed_status if confirmed_status is not None else product.get('salesStatus') or 'unknown',
            })
        scope = {'category': {'code': f"company:{company['id']}", 'name': company['name']}, 'company': {'id': company['id'], 'name': company['name'], 'officialSiteUrl': company.get('officialSiteUrl'), 'documentEntryUrl': company.get('documentEntryUrl')}, 'products': task_products}
        def create():
            with self._connect() as connection:
                try:
                    connection.begin()
                    with connection.cursor() as cursor:
                        cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents','queued',%s,%s)", (json.dumps(scope, ensure_ascii=False), json.dumps(config)))
                        task_id = cursor.lastrowid
                        keys = [f"company:{company['id']}:product:{product['id']}" for product in products] or [f"company:{company['id']}:1"]
                        cursor.executemany("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", [(task_id, key) for key in keys])
                    connection.commit()
                    return task_id
                except Exception:
                    connection.rollback()
                    raise
        task_id = await asyncio.to_thread(create)
        await self._enqueue_task(task_id)
        return await asyncio.to_thread(self.get, task_id)

    async def create_company_site_discovery_task(self, companies):
        settings = await asyncio.to_thread(self.settings.get)
        config = {key: settings[key] for key in ('globalConcurrency', 'domainIntervalMs', 'requestTimeoutMs', 'retryAttempts', 'associationCrawlEnabled')}
        scope = {'category': {'code': 'company_site', 'name': '公司官网发现'}, 'companies': [{'id': company['id'], 'name': company['name']} for company in companies]}
        def create():
            with self._connect() as connection:
                try:
                    connection.begin()
                    with connection.cursor() as cursor:
                        cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_site_discovery','queued',%s,%s)", (json.dumps(scope, ensure_ascii=False), json.dumps(config)))
                        task_id = cursor.lastrowid
                        cursor.executemany("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", [(task_id, f"company-site:{company['id']}") for company in companies])
                    connection.commit()
                    return task_id
                except Exception:
                    connection.rollback()
                    raise
        task_id = await asyncio.to_thread(create)
        await self._enqueue_task(task_id)
        return await asyncio.to_thread(self.get, task_id)

    async def resume(self, task_id):
        await asyncio.to_thread(self._reset_task, task_id, ('completed', 'queued'))
        await self._enqueue_task(task_id)
        return await asyncio.to_thread(self.get, task_id)

    async def retry_failed(self, task_id):
        def create_child():
            with self._connect() as connection:
                try:
                    connection.begin()
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT * FROM crawl_task WHERE id=%s', (task_id,))
                        row = cursor.fetchone()
                        if row is None:
                            raise TaskNotFoundError(task_id)
                        task = public_row(row)
                        cursor.execute("SELECT * FROM crawl_task_unit WHERE task_id=%s AND state='failed' ORDER BY page", (task_id,))
                        failed = cursor.fetchall()
                        if not failed:
                            raise TaskNotControllableError(f'任务 {task_id} 当前不可操作：没有失败单元')
                        scope = dict(task['scope'])
                        product_ids = set()
                        company_ids = set()
                        for unit in failed:
                            product_match = re.fullmatch(r'company:\d+:product:(\d+)', unit['unit_key'])
                            company_match = re.fullmatch(r'company-site:(\d+)', unit['unit_key'])
                            if product_match: product_ids.add(int(product_match.group(1)))
                            if company_match: company_ids.add(int(company_match.group(1)))
                        if scope.get('companies') and company_ids:
                            scope['companies'] = [company for company in scope['companies'] if company['id'] in company_ids]
                        elif scope.get('company') and product_ids and scope.get('products'):
                            scope['products'] = [product for product in scope['products'] if product['id'] in product_ids]
                        cursor.execute("INSERT INTO crawl_task (type,status,scope,config,parent_task_id) VALUES (%s,'queued',%s,%s,%s)", (task['type'], json.dumps(scope, ensure_ascii=False), json.dumps(task['config'], ensure_ascii=False) if task['config'] is not None else None, task_id))
                        child_id = cursor.lastrowid
                        cursor.executemany("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,`cursor`) VALUES (%s,%s,%s,'pending',%s)", [(child_id, unit['unit_key'], unit['page'], unit['cursor']) for unit in failed])
                    connection.commit()
                    return child_id
                except Exception:
                    connection.rollback()
                    raise
        child_id = await asyncio.to_thread(create_child)
        await self._enqueue_task(child_id)
        return await asyncio.to_thread(self.get, child_id)
