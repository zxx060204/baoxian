import asyncio
import logging
import os
import signal

import httpx
import redis.asyncio as redis

from .association import AssociationAdapter, AssociationGateway, RateLimitedAssociationGateway
from .fixture_association import FixtureAssociationGateway
from .company_gateway import CompanyHttpGateway
from .company_registry import CompanyRegistry
from .company_worker import CompanyDocumentProcessor
from .disclosure import CompanyDisclosureGateway
from .document_ingestion import DocumentIngestionService
from .downloader import download_pdf
from .index_worker import IndexTaskProcessor
from .index_refresh import IndexRefreshScheduler
from .queue import BullMQTaskQueue
from .ingestion import ProductIngestionService
from .rate_limit import RedisCompanyLock, RedisDomainRateLimiter
from .review_reporter import MySQLReviewReporter
from .settings import MySQLSettingsService
from .schedules import MySQLScheduleService
from .site_worker import CompanySiteDiscoveryProcessor
from .storage import MinioStorage
from .tasks import MySQLTaskService
from .worker import create_worker


def build_association(env, client, limiter, settings):
    if (env.get('ASSOCIATION_SOURCE', '').strip() or 'real') == 'fixture':
        return AssociationAdapter(FixtureAssociationGateway())
    return AssociationAdapter(RateLimitedAssociationGateway(AssociationGateway(client), limiter, 'tiaokuan.iachina.cn', lambda: settings.get()['domainIntervalMs']))


class WorkerRuntime:
    def __init__(self, worker, redis_client, client, owns_client, queue):
        self.worker, self.redis, self.client, self.owns_client, self.queue = worker, redis_client, client, owns_client, queue

    async def close(self):
        try:
            await self.worker.close()
        finally:
            try:
                await self.queue.close()
            finally:
                try:
                    await self.redis.aclose()
                finally:
                    if self.owns_client:
                        await self.client.aclose()


def register_shutdown_signals(loop, stopped):
    names = ['SIGINT', 'SIGTERM']
    if hasattr(signal, 'SIGBREAK'):
        names.append('SIGBREAK')
    for name in names:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, stopped.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(signum, lambda _signum, _frame: loop.call_soon_threadsafe(stopped.set))
            except (OSError, RuntimeError, ValueError):
                logging.getLogger(__name__).exception('无法注册 Worker 关闭信号 %s', name)


async def create_runtime(env, *, client=None, storage=None, download=None, enable_refresh=True, enable_recovery=True, worker_options=None):
    def required(name):
        value = env.get(name, '').strip()
        if not value: raise ValueError(f'缺少 {name}')
        return value

    product_url, crawl_url, redis_url = required('DATABASE_URL'), required('CRAWL_DATABASE_URL'), required('REDIS_URL')
    settings = MySQLSettingsService(crawl_url)
    initial_settings = await asyncio.to_thread(settings.get)
    owns_client = client is None
    client = client or httpx.AsyncClient()
    redis_client = redis.from_url(redis_url)
    queue = None
    try:
        limiter = RedisDomainRateLimiter(redis_client)
        review = MySQLReviewReporter(crawl_url)
        gateway = CompanyHttpGateway(client, settings.get, limiter.wait)
        if storage is None:
            storage = MinioStorage(env.get('MINIO_ENDPOINT', '127.0.0.1'), int(env.get('MINIO_PORT', '9000')), env.get('MINIO_USE_SSL', 'false') == 'true', env.get('MINIO_ACCESS_KEY', 'baoxian'), env.get('MINIO_SECRET_KEY', 'baoxian_minio_dev'), env.get('MINIO_BUCKET', 'baoxian-documents'))
        if download is None:
            async def download(url, **kwargs):
                return await download_pdf(url, client=client, **kwargs)
        ingestion = DocumentIngestionService(product_url, crawl_url, storage, download)
        association = build_association(env, client, limiter, settings)
        async def wait_for_disclosure():
            values = await asyncio.to_thread(settings.get)
            await limiter.wait('icidp.iachina.cn', values['domainIntervalMs'])
        disclosure = CompanyDisclosureGateway(client, wait_for_disclosure)
        processors = {
            'association_index': IndexTaskProcessor(crawl_url, association, ProductIngestionService(product_url, review, crawl_url), review, settings),
            'company_documents': CompanyDocumentProcessor(crawl_url, CompanyRegistry(gateway), ingestion, review, limiter),
            'company_site_discovery': CompanySiteDiscoveryProcessor(crawl_url, product_url, disclosure, review),
        }
        queue_name = env.get('CRAWL_QUEUE_NAME', '').strip() or 'crawl'
        queue = BullMQTaskQueue(redis_url, queue_name)
        task_service = MySQLTaskService(crawl_url, settings=settings, queue=queue, product_database_url=product_url)
        if enable_recovery:
            await task_service.reconcile_worker_state()
        refresh = IndexRefreshScheduler(crawl_url, settings, task_service)
        if enable_refresh:
            await queue.queue.upsertJobScheduler('association-refresh-check', {'every': 60000}, job_name='association_refresh_check', job_data={}, opts={'removeOnComplete': 10, 'removeOnFail': 100})
        if enable_recovery:
            await queue.queue.upsertJobScheduler('task-state-recovery', {'every': 60000}, job_name='task_state_recovery_check', job_data={}, opts={'removeOnComplete': 10, 'removeOnFail': 100})
        worker = create_worker(queue_name, redis_url, task_service, processors, concurrency=initial_settings['globalConcurrency'], company_lock=RedisCompanyLock(redis_client), refresh_scheduler=refresh, schedule_service=MySQLScheduleService(crawl_url, queue), worker_options=worker_options)
        return WorkerRuntime(worker, redis_client, client, owns_client, queue)
    except BaseException:
        resources = []
        if queue is not None:
            resources.append(('队列', queue.close))
        resources.append(('Redis', redis_client.aclose))
        if owns_client:
            resources.append(('HTTP 客户端', client.aclose))
        for name, close in resources:
            try:
                await close()
            except BaseException:
                logging.getLogger(__name__).exception('Worker 启动失败后关闭%s失败', name)
        raise


async def main():
    runtime = await create_runtime(os.environ)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    register_shutdown_signals(loop, stopped)
    try:
        await stopped.wait()
    finally:
        await runtime.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
