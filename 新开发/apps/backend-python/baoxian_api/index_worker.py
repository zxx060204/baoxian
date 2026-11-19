import asyncio
import json
from datetime import date, datetime, timezone

from .companies import MySQLCompanyService
from .tasks import MySQLTaskService, TaskNotFoundError
from .settings import MySQLSettingsService


class IndexTaskProcessor(MySQLCompanyService):
    def __init__(self, crawl_database_url, adapter, ingestion, review, settings=None):
        super().__init__(crawl_database_url)
        self.tasks = MySQLTaskService(crawl_database_url)
        self.adapter = adapter
        self.ingestion = ingestion
        self.review = review
        self.settings = settings or MySQLSettingsService(crawl_database_url)

    async def process(self, task_id):
        task = self.tasks.get(task_id)
        if task['status'] in ('completed', 'cancelled'):
            return
        category = task['scope']['category']
        code = category['code']
        page = 1
        cursor_value = None
        seen = set()
        base_unique = task['uniqueCount']
        read_count = task['readCount']
        reported_total = task['reportedTotal']
        pages_processed = sum(unit['state'] == 'completed' for unit in task['units']) if task['scope'].get('maxPages') else 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE crawl_task SET status = 'running', started_at = COALESCE(started_at, UTC_TIMESTAMP()), updated_at = UTC_TIMESTAMP() WHERE id = %s AND status NOT IN ('completed', 'cancelled')", (task_id,))
                if not cursor.rowcount:
                    return
                try:
                    while True:
                        if task['scope'].get('maxPages') and pages_processed >= task['scope']['maxPages']:
                            break
                        if self.tasks.get(task_id)['status'] == 'cancelled':
                            return
                        connection.begin()
                        await asyncio.to_thread(cursor.execute, 'INSERT INTO association_index_progress (category_code,category_name) VALUES (%s,%s) ON DUPLICATE KEY UPDATE category_name = VALUES(category_name)', (code, category['name']))
                        await asyncio.to_thread(cursor.execute, 'SELECT * FROM association_index_progress WHERE category_code = %s FOR UPDATE', (code,))
                        progress = cursor.fetchone()
                        if self.tasks.get(task_id)['status'] == 'cancelled':
                            connection.rollback()
                            return
                        if progress['completed_at'] is not None:
                            elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - progress['completed_at']).total_seconds()
                            if elapsed < self.settings.get()['indexRefreshMinutes'] * 60:
                                cursor.execute("DELETE FROM crawl_task_unit WHERE task_id=%s AND state='pending'", (task_id,))
                                task['scope']['indexSkipReason'] = 'not_due'
                                cursor.execute('UPDATE crawl_task SET scope=%s, updated_at=UTC_TIMESTAMP() WHERE id=%s', (json.dumps(task['scope'], ensure_ascii=False), task_id))
                                connection.commit()
                                break
                            cursor.execute('UPDATE association_index_progress SET next_page=1, '+chr(96)+'cursor'+chr(96)+'=NULL, cycle=cycle+1, completed_at=NULL, updated_at=UTC_TIMESTAMP() WHERE category_code=%s', (code,))
                            page, cursor_value = 1, None
                        else:
                            page = progress['next_page']
                            cursor_value = progress['cursor']
                        config = task['config'] or {}
                        attempts = max(1, config.get('retryAttempts', 3))
                        for attempt in range(attempts):
                            try:
                                result = await asyncio.wait_for(self.adapter.read_page(code, cursor_value), timeout=config.get('requestTimeoutMs', 30000) / 1000)
                                break
                            except Exception:
                                if attempt + 1 == attempts:
                                    raise
                        if self.tasks.get(task_id)['status'] == 'cancelled':
                            connection.rollback()
                            return
                        await asyncio.to_thread(cursor.execute, 'SELECT status FROM crawl_task WHERE id=%s FOR UPDATE', (task_id,))
                        locked_task = cursor.fetchone()
                        if not locked_task or locked_task['status'] == 'cancelled':
                            connection.rollback()
                            return
                        if page != 1:
                            cursor.execute("DELETE FROM crawl_task_unit WHERE task_id=%s AND page=1 AND state='pending'", (task_id,))
                        read_count += len(result['products'])
                        if result.get('reportedTotal') is not None:
                            reported_total = result['reportedTotal']
                        selected = []
                        cutoff = False
                        for record in result['products']:
                            if task['scope'].get('saleAfter'):
                                raw = (record.get('saleDateRaw') or '').strip()
                                try:
                                    valid = len(raw) == 10 and date.fromisoformat(raw).isoformat() == raw
                                except ValueError:
                                    valid = False
                                if not valid:
                                    raise ValueError(f"销售时间缺失或无效，无法判断筛选边界：{record['productName']}")
                                if raw <= task['scope']['saleAfter']:
                                    cutoff = True
                                    break
                            selected.append(record)
                        for record in selected:
                            key = record.get('sourceRecordId') or record['companyName'] + '|' + record['productName']
                            if key in seen:
                                continue
                            seen.add(key)
                            outcome = await asyncio.to_thread(self.ingestion.upsert, record, category, task_id)
                            if outcome['status'] == 'ambiguous':
                                await asyncio.to_thread(self.review.record_product_identity, task_id, outcome['incoming'], outcome['candidates'])
                            else:
                                cursor.execute('INSERT IGNORE INTO crawl_task_product (task_id,product_id) VALUES (%s,%s)', (task_id, outcome['productId']))
                        next_cursor = result.get('nextCursor')
                        cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,attempts, `cursor`) VALUES (%s,%s,%s,'completed',1,%s) ON DUPLICATE KEY UPDATE state='completed',attempts=attempts+1,`cursor`=VALUES(`cursor`),updated_at=UTC_TIMESTAMP()", (task_id, f'{code}:{page}', page, cursor_value if cutoff else next_cursor))
                        cursor.execute('UPDATE crawl_task SET read_count=%s, unique_count=%s, reported_total=%s, updated_at=UTC_TIMESTAMP() WHERE id=%s', (read_count, base_unique + len(seen), reported_total, task_id))
                        if cutoff:
                            task['scope']['saleDateStopReason'] = 'cutoff'
                            cursor.execute('UPDATE crawl_task SET scope=%s WHERE id=%s', (json.dumps(task['scope'], ensure_ascii=False), task_id))
                        else:
                            cursor.execute('UPDATE association_index_progress SET next_page=%s, `cursor`=%s, completed_at=' + ('NULL' if next_cursor else 'UTC_TIMESTAMP()') + ', updated_at=UTC_TIMESTAMP() WHERE category_code=%s', (page + 1 if next_cursor else page, next_cursor, code))
                        connection.commit()
                        pages_processed += 1
                        if cutoff or not next_cursor:
                            break
                    cursor.execute("UPDATE crawl_task SET status='completed', finished_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP() WHERE id=%s AND status<>'cancelled'", (task_id,))
                except Exception as error:
                    connection.rollback()
                    try:
                        current = self.tasks.get(task_id)
                    except TaskNotFoundError:
                        return
                    if current['status'] == 'cancelled':
                        return
                    cursor.execute("INSERT INTO crawl_task_unit (task_id,unit_key,page,state,last_error) VALUES (%s,%s,%s,'failed',%s) ON DUPLICATE KEY UPDATE state='failed',last_error=VALUES(last_error),updated_at=UTC_TIMESTAMP()", (task_id, f'{code}:{page}', page, str(error)))
                    cursor.execute("SELECT COUNT(*) AS count FROM crawl_task_unit WHERE task_id=%s AND state='completed'", (task_id,))
                    status = 'partial_failed' if cursor.fetchone()['count'] else 'failed'
                    cursor.execute("UPDATE crawl_task SET status=%s,finished_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status<>'cancelled'", (status, task_id))
                    raise


def create_index_worker(queue_name, redis_url, processor, concurrency=1):
    from bullmq import Worker

    async def consume(job, token):
        if job.name != 'association_index':
            raise ValueError(f'不支持的任务类型：{job.name}')
        task_id = job.data.get('taskId')
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
            raise ValueError('任务数据缺少有效 taskId')
        task = await asyncio.to_thread(processor.tasks.get, task_id)
        if task['type'] != 'association_index':
            raise ValueError(f"索引 Worker 不支持任务类型：{task['type']}")
        await processor.process(task_id)

    return Worker(queue_name, consume, {'connection': redis_url, 'concurrency': concurrency})
