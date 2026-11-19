import asyncio
import json
from urllib.parse import urlsplit

from .companies import MySQLCompanyService
from .tasks import MySQLTaskService, TaskNotFoundError


class _TaskCancelled(Exception):
    pass


class CompanyDocumentProcessor(MySQLCompanyService):
    def __init__(self, crawl_database_url, adapter, ingestion, review, rate_limiter=None):
        super().__init__(crawl_database_url)
        self.tasks = MySQLTaskService(crawl_database_url)
        self.adapter = adapter
        self.ingestion = ingestion
        self.review = review
        self.rate_limiter = rate_limiter

    def _execute(self, sql, args):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
                return cursor.rowcount

    async def _check_cancelled(self, task_id):
        try:
            task = await asyncio.to_thread(self.tasks.get, task_id)
        except TaskNotFoundError:
            raise _TaskCancelled()
        if task['status'] == 'cancelled':
            raise _TaskCancelled()
        return task

    async def _await_with_cancellation(self, task_id, operation):
        running = asyncio.create_task(operation)
        try:
            while not running.done():
                await asyncio.wait({running}, timeout=.5)
                await self._check_cancelled(task_id)
            return await running
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def process(self, task_id):
        task = await asyncio.to_thread(self.tasks.get, task_id)
        if task['status'] in ('completed', 'cancelled'):
            return
        company = task['scope'].get('company')
        products = task['scope'].get('products', [])
        if not company:
            raise ValueError('公司资料任务缺少公司范围')
        previous_statuses = {}
        async def restore_previous():
            for product_id, previous in previous_statuses.items():
                if previous is not None:
                    await self.ingestion.restore_product_integration(product_id, task_id, previous)

        try:
            for product in products:
                await self._check_cancelled(task_id)
                previous_statuses[product['id']] = await self.ingestion.begin_product_integration(product['id'], task_id)
            changed = await asyncio.to_thread(self._execute, "UPDATE crawl_task SET status='running',started_at=COALESCE(started_at,UTC_TIMESTAMP()),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status NOT IN ('completed','cancelled')", (task_id,))
            if not changed:
                await restore_previous()
                return
            result = await self._await_with_cancellation(task_id, self.adapter.discover_documents(company, products))
            failed_count = 0
            failed_ids, failed_errors, downloaded_ids, pending_ids = set(), {}, set(), set()
            unsearched_ids = set(result.get('unsearchedProductIds') or [])
            unsearched_products = [
                {'id': product['id'], 'name': product['name'], 'reason': '本轮未搜索该产品销售状态'}
                for product in products if product['id'] in unsearched_ids
            ]
            page_failures = result.get('pageFailures') or []
            searched_ids = set(result.get('searchedProductIds') or [product['id'] for product in products if product['id'] not in unsearched_ids])
            failed_search_ids = set()
            for failure in page_failures:
                affected_ids = failure.get('productIds')
                failed_search_ids.update(affected_ids if affected_ids is not None else searched_ids)
            for candidate in result['candidates']:
                await self._check_cancelled(task_id)
                if self.rate_limiter is not None:
                    await self._await_with_cancellation(task_id, self.rate_limiter.wait(urlsplit(candidate['downloadUrl']).netloc, (task.get('config') or {}).get('domainIntervalMs', 0)))
                ids = candidate['productIds']
                shared = candidate.get('shared') is True and len(ids) > 1
                product_id = ids[0] if len(ids) == 1 else None
                config = task.get('config') or {}
                ingested = await self._await_with_cancellation(task_id, self.ingestion.ingest_candidate(company['id'], candidate, product_id=product_id, task_id=task_id, retry_attempts=config.get('retryAttempts', 1), request_timeout_ms=config.get('requestTimeoutMs', 30000)))
                if ingested['status'] == 'failed':
                    failed_count += 1
                    for failed_id in ids:
                        failed_ids.add(failed_id)
                        failed_errors[failed_id] = f"{ingested['message']} · {candidate['downloadUrl']}"
                elif product_id and candidate.get('versionStatus') != 'historical':
                    downloaded_ids.add(product_id)
                if shared and ingested['status'] == 'stored':
                    for shared_id in ids:
                        if candidate.get('versionStatus') != 'historical': downloaded_ids.add(shared_id)
                        await self.ingestion.link_to_product(shared_id, ingested['documentId'], ingested['sourceId'], candidate, task_id=task_id)
                if shared and ingested['status'] == 'failed':
                    for shared_id in ids:
                        await self.ingestion.mark_download_failed(shared_id, candidate['type'], task_id, ingested['message'])
                if len(ids) != 1 and not shared:
                    if ingested['status'] == 'stored':
                        for pending_id in ids:
                            pending_ids.add(pending_id)
                            await self.ingestion.mark_attribution_pending(pending_id, candidate['type'], task_id)
                        await asyncio.to_thread(self.review.record_document_attribution, task_id, candidate, ingested['documentId'], ingested['sourceId'])
                    else:
                        for failed_id in ids:
                            await self.ingestion.mark_download_failed(failed_id, candidate['type'], task_id, ingested['message'])
            await asyncio.to_thread(self._execute, "UPDATE crawl_task SET scope=JSON_SET(scope, '$.searchedPages', CAST(%s AS JSON), '$.searchedSalesStatuses', CAST(%s AS JSON), '$.searchedProductIds', CAST(%s AS JSON), '$.notFoundProductIds', CAST(%s AS JSON), '$.unsearchedProducts', CAST(%s AS JSON)),updated_at=UTC_TIMESTAMP() WHERE id=%s", (
                json.dumps(result['searchedPages'], ensure_ascii=False),
                json.dumps(result.get('searchedSalesStatuses') or [], ensure_ascii=False),
                json.dumps(result.get('searchedProductIds') or []),
                json.dumps(result.get('notFoundProductIds') or []),
                json.dumps(unsearched_products, ensure_ascii=False),
                task_id,
            ))
            for product in products:
                if product['id'] in unsearched_ids or product['id'] in failed_search_ids:
                    continue
                for kind in ('clause', 'rate_table', 'cash_value_table', 'prospectus'):
                    await self.ingestion.mark_not_found(product['id'], kind, task_id)
            await self._check_cancelled(task_id)
            await asyncio.to_thread(self._execute, "UPDATE crawl_task_unit SET state='completed',updated_at=UTC_TIMESTAMP() WHERE task_id=%s", (task_id,))
            if page_failures:
                for failure in page_failures:
                    message = f"{failure['url']}: {failure['message']}"
                    for failed_id in failure.get('productIds') or searched_ids:
                        failed_ids.add(failed_id)
                        failed_errors[failed_id] = message
            for failed_id in failed_ids:
                await asyncio.to_thread(self._execute, "UPDATE crawl_task_unit SET state='failed',last_error=%s,updated_at=UTC_TIMESTAMP() WHERE task_id=%s AND unit_key=%s", (failed_errors.get(failed_id, '候选文件下载失败'), task_id, f"company:{company['id']}:product:{failed_id}"))
            final_status = 'partial_failed' if page_failures or unsearched_ids else 'completed' if failed_count == 0 else 'failed' if failed_count >= len(result['candidates']) else 'partial_failed'
            await asyncio.to_thread(self._execute, "UPDATE crawl_task SET status=%s,read_count=%s,unique_count=%s,finished_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status<>'cancelled'", (final_status, len(result['candidates']), len(result['candidates']), task_id))
            final_task = await asyncio.to_thread(self.tasks.get, task_id)
            if final_task['status'] != final_status:
                return
            for product in products:
                product_id = product['id']
                if product_id in unsearched_ids:
                    previous = previous_statuses.get(product_id)
                    if previous is not None:
                        await self.ingestion.restore_product_integration(product_id, task_id, previous)
                    continue
                status = 'partial_failed' if product_id in failed_search_ids else 'completed' if product_id in downloaded_ids else 'partial_failed' if product_id in pending_ids else 'failed' if product_id in failed_ids else 'not_found'
                reason = '部分官网页面读取失败' if product_id in failed_search_ids else '存在归属待确认资料' if status == 'partial_failed' else '本轮已搜索范围未找到资料' if status == 'not_found' else None
                await self.ingestion.set_product_integration_status(product_id, status, task_id, reason)
        except _TaskCancelled:
            await restore_previous()
            return
        except Exception as error:
            try:
                await self._check_cancelled(task_id)
            except _TaskCancelled:
                await restore_previous()
                return
            for product in products:
                await self.ingestion.set_product_integration_status(product['id'], 'failed', task_id, str(error))
            await asyncio.to_thread(self._execute, "UPDATE crawl_task_unit SET state='failed',last_error=%s,updated_at=UTC_TIMESTAMP() WHERE task_id=%s", (str(error), task_id))
            await asyncio.to_thread(self._execute, "UPDATE crawl_task SET status='failed',finished_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s AND status<>'cancelled'", (task_id,))
            raise
