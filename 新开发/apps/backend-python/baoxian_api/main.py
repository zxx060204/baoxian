import os

from .app import create_app
from .companies import MySQLCompanyService
from .products import MySQLProductService
from .settings import MySQLSettingsService
from .review_reporter import MySQLReviewReporter
from .documents import DocumentService
from .storage import MinioStorage
from .schedules import MySQLScheduleService
from .tasks import MySQLTaskService
from .queue import OnDemandBullMQTaskQueue
from .index_refresh import IndexRefreshScheduler


def build_from_env(env):
    token = env.get("INTERNAL_API_TOKEN", "").strip()
    database_url = env.get("DATABASE_URL", "").strip()
    crawl_database_url = env.get("CRAWL_DATABASE_URL", "").strip()
    redis_url = env.get("REDIS_URL", "").strip()
    if not token:
        raise ValueError("缺少 INTERNAL_API_TOKEN")
    if not database_url:
        raise ValueError("缺少 DATABASE_URL")
    if not crawl_database_url:
        raise ValueError("缺少 CRAWL_DATABASE_URL")
    if not redis_url:
        raise ValueError("缺少 REDIS_URL")
    settings = MySQLSettingsService(crawl_database_url)
    queue = OnDemandBullMQTaskQueue(redis_url, env.get('CRAWL_QUEUE_NAME', '').strip() or 'crawl')
    storage = MinioStorage(env.get('MINIO_ENDPOINT', '127.0.0.1'), int(env.get('MINIO_PORT', '9000')), env.get('MINIO_USE_SSL', 'false') == 'true', env.get('MINIO_ACCESS_KEY', 'baoxian'), env.get('MINIO_SECRET_KEY', 'baoxian_minio_dev'), env.get('MINIO_BUCKET', 'baoxian-documents'))
    tasks = MySQLTaskService(crawl_database_url, settings=settings, queue=queue, product_database_url=database_url)
    return create_app(internal_api_token=token, refresh_scheduler=IndexRefreshScheduler(crawl_database_url, settings, tasks), review_service=MySQLReviewReporter(crawl_database_url, database_url), document_service=DocumentService(database_url, crawl_database_url, storage), company_service=MySQLCompanyService(database_url), product_service=MySQLProductService(database_url, crawl_database_url), settings_service=settings, schedule_service=MySQLScheduleService(crawl_database_url, queue), task_service=tasks)


def create_application():
    return build_from_env(os.environ)
