from datetime import date, datetime, timezone
import asyncio
import logging
from contextlib import asynccontextmanager
import json
import platform
import time
from typing import Annotated, Literal

from fastapi import FastAPI, Request, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response, HTMLResponse
from urllib.parse import quote
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, model_validator

from .review_reporter import ReviewItemNotFoundError, ReviewItemAlreadyResolvedError, InvalidReviewResolutionError
from .documents import (
    DocumentNotFoundError, DocumentSourceNotFoundError, NumericLayoutError, TextLayoutError,
    UnsupportedNumericDocumentError, UnsupportedTextDocumentError, LayoutStorageError,
    SavedLayoutNotFoundError, UnsupportedLayoutDocumentError,
)
from .companies import CompanyNotFoundError, DuplicateCompanyError
from .products import ProductNotFoundError
from .schedules import DuplicateScheduleError, ScheduleNotFoundError
from .tasks import AssociationDisabledError, QueueUnavailableError, TaskNotControllableError, TaskNotFoundError


class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    officialSiteUrl: str | None = Field(default=None, max_length=1024)
    documentEntryUrl: str | None = Field(default=None, max_length=1024)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("公司名称不能为空")
        return value

    @field_validator("officialSiteUrl", "documentEntryUrl")
    @classmethod
    def http_url(cls, value):
        if value is not None:
            value = value.strip()
            if value and not value.lower().startswith(("http://", "https://")):
                raise ValueError("必须是 http(s) 地址")
        return value


class ProductCorrection(BaseModel):
    name: str | None = None
    salesStatus: Literal['on_sale', 'off_sale', 'disabled', 'stopped', 'unknown'] | None = None
    correctionNote: str | None = None

    @model_validator(mode='before')
    @classmethod
    def validate_input(cls, value):
        if not isinstance(value, dict) or not any(key in value for key in ('name', 'salesStatus', 'correctionNote')):
            raise ValueError('至少提供一个更正项')
        for key in ('name', 'salesStatus', 'correctionNote'):
            if key in value and value[key] is None:
                raise ValueError('更正项不能为 null')
        return value

    @field_validator('name', 'correctionNote')
    @classmethod
    def clean_text(cls, value, info):
        value = value.strip()
        if (info.field_name == 'name' and (not value or len(value) > 255)) or (info.field_name == 'correctionNote' and len(value) > 2000):
            raise ValueError('更正内容无效')
        return value


class IndexTaskInput(BaseModel):
    type: Literal['association_index']
    category: str
    maxPages: int | None = Field(default=None, strict=True, ge=1, le=1000)
    saleAfter: str | None = None

    @field_validator('maxPages', 'saleAfter', mode='before')
    @classmethod
    def reject_null_scope(cls, value):
        if value is None:
            raise ValueError('可选范围不能为 null')
        return value

    @field_validator('category')
    @classmethod
    def valid_category(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('类别不能为空')
        return value

    @field_validator('saleAfter')
    @classmethod
    def valid_sale_date(cls, value):
        if value is not None and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
            raise ValueError('销售日期无效')
        return value


class CompanyDocumentsTaskInput(BaseModel):
    type: Literal['company_documents']
    companyId: int = Field(gt=0)
    productIds: list[Annotated[int, Field(gt=0)]] | None = Field(default=None, min_length=1)

    @field_validator('productIds', mode='before')
    @classmethod
    def reject_null_product_ids(cls, value):
        if value is None:
            raise ValueError('产品范围不能为 null')
        return value


class CompanySiteDiscoveryInput(BaseModel):
    type: Literal['company_site_discovery']
    companyIds: list[Annotated[int, Field(gt=0)]] | None = Field(default=None, min_length=1)

    @field_validator('companyIds', mode='before')
    @classmethod
    def reject_null_company_ids(cls, value):
        if value is None:
            raise ValueError('公司范围不能为 null')
        return value


class SettingsUpdate(BaseModel):
    globalConcurrency: int | None = Field(default=None, strict=True, ge=1, le=10)
    domainIntervalMs: int | None = Field(default=None, strict=True, ge=100, le=60000)
    requestTimeoutMs: int | None = Field(default=None, strict=True, ge=1000, le=120000)
    retryAttempts: int | None = Field(default=None, strict=True, ge=0, le=10)
    indexRefreshMinutes: int | None = Field(default=None, strict=True, ge=1, le=525600)
    associationCrawlEnabled: bool | None = Field(default=None, strict=True)

    @model_validator(mode='before')
    @classmethod
    def validate_input(cls, value):
        keys = ('globalConcurrency', 'domainIntervalMs', 'requestTimeoutMs', 'retryAttempts', 'indexRefreshMinutes', 'associationCrawlEnabled')
        if not isinstance(value, dict) or not any(key in value for key in keys):
            raise ValueError('至少提供一个设置项')
        if any(key in value and value[key] is None for key in keys):
            raise ValueError('设置项不能为 null')
        return value



class ReviewResolution(BaseModel):
    action: Literal['merge', 'new', 'assign', 'ignore', 'adopt', 'keep', 'select_site']
    productId: int | None = Field(default=None, gt=0)
    siteUrl: str | None = None

    @model_validator(mode='after')
    def validate_action(self):
        if self.action in ('merge', 'assign') and self.productId is None: raise ValueError('候选产品 ID 无效')
        if self.action == 'select_site':
            from urllib.parse import urlsplit
            try:
                parsed = urlsplit((self.siteUrl or '').strip())
                if parsed.scheme not in ('http', 'https') or not parsed.netloc: raise ValueError()
            except ValueError:
                raise ValueError('官网地址无效')
            self.siteUrl = self.siteUrl.strip()
        return self


class ScheduleCreate(BaseModel):
    category: str = Field(min_length=1)
    everyMinutes: int = Field(strict=True, ge=1, le=1440)
    enabled: bool = Field(default=True, strict=True)

    @field_validator('category')
    @classmethod
    def clean_category(cls, value):
        value = value.strip()
        if not value: raise ValueError('类别不能为空')
        return value


class ScheduleUpdate(BaseModel):
    everyMinutes: int | None = Field(default=None, strict=True, ge=1, le=1440)
    enabled: bool | None = Field(default=None, strict=True)

    @model_validator(mode='before')
    @classmethod
    def validate_changes(cls, value):
        if not isinstance(value, dict) or not any(key in value for key in ('everyMinutes', 'enabled')):
            raise ValueError('至少提供一个设置项')
        if any(key in value and value[key] is None for key in ('everyMinutes', 'enabled')):
            raise ValueError('设置项不能为 null')
        return value

def create_app(*, internal_api_token: str, company_service=None, product_service=None, settings_service=None, task_service=None, schedule_service=None, document_service=None, review_service=None, refresh_scheduler=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        sync = getattr(schedule_service, 'sync_schedulers', None)
        if sync is not None:
            try:
                await sync()
            except Exception:
                logging.getLogger(__name__).exception('定时计划初始化失败')
        yield

    app = FastAPI(lifespan=lifespan)
    started_at = time.monotonic()

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=400, content={"code": "validation_error", "message": "请求参数无效", "details": [{"path": ".".join(str(part) for part in issue["loc"] if part not in ("body", "path", "query")), "message": issue["msg"]} for issue in error.errors()]})

    @app.exception_handler(ReviewItemNotFoundError)
    async def review_not_found(request: Request, error: ReviewItemNotFoundError):
        return JSONResponse(status_code=404, content={'code': 'not_found', 'message': f'待确认项不存在：{error}'})

    @app.exception_handler(ReviewItemAlreadyResolvedError)
    async def review_resolved(request: Request, error: ReviewItemAlreadyResolvedError):
        return JSONResponse(status_code=409, content={'code': 'review_resolved', 'message': f'待确认项已处理：{error}'})

    @app.exception_handler(InvalidReviewResolutionError)
    async def invalid_resolution(request: Request, error: InvalidReviewResolutionError):
        return JSONResponse(status_code=400, content={'code': 'invalid_resolution', 'message': str(error)})

    @app.exception_handler(DocumentNotFoundError)
    async def document_not_found_handler(request: Request, error: DocumentNotFoundError):
        return JSONResponse(status_code=404, content={'code': 'not_found', 'message': f'文件不存在：{error}'})

    @app.exception_handler(DocumentSourceNotFoundError)
    async def document_source_not_found_handler(request: Request, error: DocumentSourceNotFoundError):
        return JSONResponse(status_code=404, content={'code': 'not_found', 'message': f'文件来源不存在：{error}'})

    @app.exception_handler(LayoutStorageError)
    async def layout_storage_error(request: Request, error: LayoutStorageError):
        return JSONResponse(status_code=503, content={'code': 'layout_storage_unavailable', 'message': str(error)})

    @app.exception_handler(DuplicateScheduleError)
    async def duplicate_schedule_handler(request: Request, error: DuplicateScheduleError):
        return JSONResponse(status_code=409, content={'code': 'duplicate_schedule', 'message': f'该类别已有采集计划：{error}'})

    @app.exception_handler(ScheduleNotFoundError)
    async def schedule_not_found_handler(request: Request, error: ScheduleNotFoundError):
        return JSONResponse(status_code=404, content={'code': 'not_found', 'message': f'采集计划不存在：{error}'})

    @app.exception_handler(AssociationDisabledError)
    async def association_disabled_handler(request: Request, error: AssociationDisabledError):
        return JSONResponse(status_code=409, content={'code': 'association_crawl_disabled', 'message': '协会采集开关已关闭，不能创建新的协会索引任务'})

    @app.exception_handler(QueueUnavailableError)
    async def queue_unavailable_handler(request: Request, error: QueueUnavailableError):
        return JSONResponse(status_code=503, content={'code': 'queue_unavailable', 'message': '任务队列不可用'})

    @app.exception_handler(TaskNotControllableError)
    async def task_not_controllable_handler(request: Request, error: TaskNotControllableError):
        return JSONResponse(status_code=409, content={'code': 'task_not_controllable', 'message': str(error)})

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found_handler(request: Request, error: TaskNotFoundError):
        return JSONResponse(status_code=404, content={"code": "not_found", "message": f"采集任务不存在：{error}"})

    @app.exception_handler(ProductNotFoundError)
    async def product_not_found_handler(request: Request, error: ProductNotFoundError):
        return JSONResponse(status_code=404, content={"code": "not_found", "message": f"产品不存在：{error}"})

    @app.exception_handler(CompanyNotFoundError)
    async def company_not_found_handler(request: Request, error: CompanyNotFoundError):
        return JSONResponse(status_code=404, content={"code": "not_found", "message": f"公司不存在：{error}"})

    @app.exception_handler(DuplicateCompanyError)
    async def duplicate_company_handler(request: Request, error: DuplicateCompanyError):
        return JSONResponse(status_code=409, content={"code": "duplicate_company", "message": f"公司名称已存在：{error}"})

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path != "/health":
            if request.headers.get("authorization") != f"Bearer {internal_api_token}":
                return JSONResponse(
                    status_code=401,
                    content={"code": "unauthorized", "message": "缺少或无效的内部访问令牌"},
                )
        return await call_next(request)

    if refresh_scheduler is not None:
        @app.post('/api/index-refresh/check')
        async def check_index_refresh():
            return await refresh_scheduler.check()

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "baoxian-api",
            "version": "0.1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

    @app.get("/api/system")
    async def system():
        return {
            "service": "baoxian-api",
            "version": "0.1.0",
            "python": platform.python_version(),
            "uptimeSeconds": round(time.monotonic() - started_at),
        }

    @app.get("/api/categories")
    async def categories():
        names = ("人寿保险", "年金保险", "健康保险", "意外伤害保险", "委托管理业务")
        return {"source": "association", "items": [{"code": f"PubNewProdTypeCode_{index:02d}", "name": name} for index, name in enumerate(names)]}

    if company_service is not None:
        @app.get("/api/companies")
        def list_companies():
            return {"items": company_service.list()}

        @app.post("/api/companies", status_code=201)
        def create_company(data: CompanyInput):
            return company_service.create(data.model_dump(exclude_unset=True))

        @app.patch("/api/companies/{company_id}")
        def update_company(data: CompanyInput, company_id: int = Path(gt=0)):
            return company_service.update(company_id, data.model_dump(exclude_unset=True))

    if product_service is not None:
        @app.get("/api/products")
        def list_products(
            companyId: int | None = Query(default=None, gt=0),
            status: Literal['on_sale', 'off_sale', 'disabled', 'stopped', 'unknown'] | None = None,
            q: str | None = Query(default=None, max_length=255),
            integrationStatus: Literal['not_started', 'running', 'completed', 'partial_failed', 'failed', 'not_found'] | None = None,
            limit: int | None = Query(default=None, ge=1, le=200),
            offset: int | None = Query(default=None, ge=0),
            materialType: Literal['clause', 'rate_table', 'cash_value_table', 'prospectus'] | None = None,
            materialStatus: Literal['not_run', 'not_found', 'downloaded', 'download_failed', 'attribution_pending'] | None = None,
        ):
            return {"items": product_service.list(company_id=companyId, status=status, q=q, integration_status=integrationStatus, limit=limit, offset=offset, material_type=materialType, material_status=materialStatus)}

        @app.patch("/api/products/{product_id}")
        def correct_product(data: ProductCorrection, product_id: int = Path(gt=0)):
            return product_service.update_correction(product_id, data.model_dump(exclude_unset=True))

        @app.get("/api/products/{product_id}")
        def get_product(product_id: int = Path(gt=0)):
            return product_service.get(product_id)

    if review_service is not None:
        @app.post('/api/review-items/{item_id}/resolve')
        def resolve_review(data: ReviewResolution, item_id: int = Path(gt=0)):
            return review_service.resolve(item_id, data.model_dump(exclude_unset=True))

        @app.get('/api/review-items')
        def review_items(status: Literal['pending', 'resolved', 'dismissed'] | None = None, type: Literal['product_identity', 'document_attribution', 'sales_status_diff', 'correction_diff', 'source_value_diff', 'company_site'] | None = None):
            return {'items': review_service.list(status, type)}

    if document_service is not None:
        @app.get('/api/products/{product_id}/parsed-layouts')
        async def list_product_layouts(product_id: int = Path(gt=0)):
            return {'items': await document_service.list_saved_layouts(product_id)}

        @app.post('/api/products/{product_id}/parsed-layouts')
        async def parse_product_layouts(product_id: int = Path(gt=0)):
            return {'items': await document_service.parse_product_documents(product_id)}

        @app.get('/api/documents/{document_id}/saved-layout')
        async def saved_document_layout(document_id: int = Path(gt=0)):
            try:
                body = await document_service.get_saved_layout(document_id)
            except SavedLayoutNotFoundError:
                return JSONResponse(status_code=404, content={'code': 'layout_not_found', 'message': '该资料尚无已保存的解析结果'})
            return Response(body, media_type='application/json', headers={'Cache-Control': 'no-store'})

        @app.post('/api/documents/{document_id}/saved-layout')
        async def parse_document_layout(document_id: int = Path(gt=0), force: bool = False):
            try:
                body = await document_service.parse_document(document_id, force)
            except UnsupportedLayoutDocumentError:
                return JSONResponse(status_code=415, content={'code': 'unsupported_document', 'message': '仅支持四类产品资料 PDF'})
            except (NumericLayoutError, TextLayoutError) as error:
                return JSONResponse(status_code=422, content={
                    'code': 'layout_failed', 'message': 'PDF 解析失败', 'reason': str(error),
                })
            return Response(body, media_type='application/json')

        @app.get('/api/reports/p2')
        async def report_p2(companyId: int | None = Query(default=None, gt=0), productId: int | None = Query(default=None, gt=0)):
            return await document_service.p2_report(companyId, productId)

        @app.get('/api/reports/failures')
        async def report_failures(taskId: int | None = Query(default=None, gt=0), companyId: int | None = Query(default=None, gt=0), productId: int | None = Query(default=None, gt=0), limit: int = Query(default=100, ge=1, le=500)):
            return {'items': await document_service.list_failures(taskId, companyId, productId, limit)}

        @app.get('/api/documents/{document_id}/file')
        async def document_file(document_id: int = Path(gt=0)):
            file = await document_service.get_file(document_id)
            filename = quote(file['filename'], safe='._-')
            return Response(file['body'], media_type=file['contentType'], headers={'Content-Disposition': f'inline; filename="{filename}"'})

        @app.get('/api/documents/{document_id}/sources')
        async def document_sources(document_id: int = Path(gt=0)):
            return {'items': await document_service.list_sources(document_id)}

        @app.get('/api/documents/{document_id}/layout')
        async def document_layout(document_id: int = Path(gt=0)):
            try:
                return await document_service.get_numeric_layout(document_id)
            except UnsupportedNumericDocumentError:
                return JSONResponse(status_code=415, content={
                    'code': 'unsupported_numeric_document', 'message': '仅支持费率表和现金价值表 PDF',
                })
            except NumericLayoutError as error:
                return JSONResponse(status_code=422, content={
                    'code': 'numeric_layout_failed', 'message': '表格 PDF 解析失败', 'reason': str(error),
                })

        @app.get('/api/documents/{document_id}/parsed-content')
        async def document_parsed_content(document_id: int = Path(gt=0)):
            try:
                return await document_service.get_parsed_content(document_id)
            except UnsupportedTextDocumentError:
                return JSONResponse(status_code=415, content={
                    'code': 'unsupported_text_document', 'message': '仅支持产品条款和说明书 PDF',
                })
            except TextLayoutError as error:
                return JSONResponse(status_code=422, content={
                    'code': 'text_layout_failed', 'message': '正文 PDF 解析失败', 'reason': str(error),
                })

        @app.get('/api/crawl-tasks/{task_id}/document-snapshots')
        async def document_snapshots(task_id: int = Path(gt=0)):
            return {'items': await document_service.list_task_snapshots(task_id)}

        @app.get('/api/document-sources/{source_id}/snapshot')
        async def document_snapshot(source_id: int = Path(gt=0)):
            return HTMLResponse(await document_service.get_snapshot(source_id))

    if schedule_service is not None:
        @app.get('/api/schedules')
        async def list_schedules():
            return {'items': await schedule_service.list()}

        @app.post('/api/schedules', status_code=201)
        async def create_schedule(data: ScheduleCreate):
            names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
            categories = {f'PubNewProdTypeCode_{index:02d}': name for index, name in enumerate(names)}
            if data.category not in categories:
                return JSONResponse(status_code=400, content={'code': 'unknown_category', 'message': f'未知的协会类别：{data.category}'})
            return await schedule_service.create({'code': data.category, 'name': categories[data.category]}, data.everyMinutes, data.enabled)

        @app.patch('/api/schedules/{schedule_id}')
        async def update_schedule(data: ScheduleUpdate, schedule_id: int = Path(gt=0)):
            return await schedule_service.update(schedule_id, data.model_dump(exclude_unset=True))

    if settings_service is not None:
        @app.get("/api/settings/runtime")
        def get_settings():
            return settings_service.get()

        @app.put("/api/settings/runtime")
        def update_settings(data: SettingsUpdate):
            return settings_service.update(data.model_dump(exclude_unset=True))

    if task_service is not None:
        @app.get('/api/index-overview')
        def index_overview():
            if product_service is None:
                return JSONResponse(status_code=503, content={'message': '产品服务不可用'})
            progress = {row['categoryCode']: row for row in task_service.index_progress()}
            tasks = task_service.list(task_type='association_index')
            counts = product_service.count_by_category()
            names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
            items = []
            for index, name in enumerate(names):
                code = f'PubNewProdTypeCode_{index:02d}'
                state = progress.get(code)
                category_tasks = [task for task in tasks if task['scope']['category']['code'] == code]
                latest_total = next((task['reportedTotal'] for task in category_tasks if task['reportedTotal'] is not None), None)
                completed = [task['finishedAt'] for task in category_tasks if task['status'] == 'completed' and task['finishedAt']]
                status = 'running' if any(task['status'] == 'running' for task in category_tasks) else 'queued' if any(task['status'] == 'queued' for task in category_tasks) else 'completed' if state and state['completedAt'] else 'incomplete' if state and state['nextPage'] > 1 else 'not_started'
                next_page = state['nextPage'] if state else 1
                latest = category_tasks[0] if category_tasks else None
                items.append({'code': code, 'name': name, 'status': status, 'productCount': counts.get(code, 0), 'cycle': state['cycle'] if state else 1, 'nextPage': next_page, 'completedPages': next_page if state and state['completedAt'] else max(0, next_page - 1) if state else 0, 'completedAt': state['completedAt'] if state else None, 'updatedAt': state['updatedAt'] if state else None, 'latestCompletedAt': max(completed) if completed else None, 'reportedTotal': latest_total, 'latestTask': {'id': latest['id'], 'status': latest['status']} if latest else None})
            return {'items': items}

        @app.get('/api/reports/coverage')
        def coverage_report():
            return {'items': task_service.coverage()}

        async def create_company_document_task(company_id, product_ids=None, shortcut=False):
            if company_service is None or product_service is None:
                return JSONResponse(status_code=503, content={'code': 'company_service_unavailable', 'message': '公司资料服务不可用'})
            companies = await asyncio.to_thread(company_service.list)
            company = next((row for row in companies if row['id'] == company_id), None)
            if company is None:
                return JSONResponse(status_code=404, content={'code': 'not_found', 'message': f'公司不存在：{company_id}'})
            if not company.get('officialSiteUrl'):
                return JSONResponse(status_code=400, content={'code': 'company_site_missing', 'message': '公司未配置官网，不能发起资料任务' if shortcut else '公司未配置官网'})
            products = await asyncio.to_thread(product_service.list, company_id=company_id)
            if product_ids is not None:
                selected_ids = set(product_ids)
                products = [product for product in products if product['id'] in selected_ids]
                if len(products) != len(selected_ids):
                    return JSONResponse(status_code=400, content={'code': 'unknown_product', 'message': '产品选择包含不属于该公司的产品'})
            return await task_service.create_company_task(company, products)

        @app.post('/api/companies/{company_id}/document-tasks', status_code=202)
        async def create_company_document_shortcut(company_id: int = Path(gt=0)):
            return await create_company_document_task(company_id, shortcut=True)

        @app.post('/api/crawl-tasks', status_code=202)
        async def create_task(data: Annotated[IndexTaskInput | CompanyDocumentsTaskInput | CompanySiteDiscoveryInput, Field(discriminator='type')]):
            if isinstance(data, CompanyDocumentsTaskInput):
                return await create_company_document_task(data.companyId, data.productIds)
            if isinstance(data, CompanySiteDiscoveryInput):
                if company_service is None:
                    return JSONResponse(status_code=503, content={'code': 'company_service_unavailable', 'message': '公司服务不可用'})
                companies = await asyncio.to_thread(company_service.list)
                if data.companyIds is not None:
                    selected_ids = set(data.companyIds)
                    selected = [company for company in companies if company['id'] in selected_ids]
                    if len(selected) != len(selected_ids):
                        return JSONResponse(status_code=400, content={'code': 'unknown_company', 'message': '公司选择包含不存在的公司'})
                    if any(company.get('officialSiteUrl') for company in selected):
                        return JSONResponse(status_code=400, content={'code': 'company_site_exists', 'message': '公司已配置官网，不覆盖现有值'})
                else:
                    selected = [company for company in companies if not company.get('officialSiteUrl')]
                if not selected:
                    return JSONResponse(status_code=400, content={'code': 'no_company_to_discover', 'message': '没有需要发现官网的公司'})
                return await task_service.create_company_site_discovery_task(selected)
            names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
            categories = {f'PubNewProdTypeCode_{index:02d}': name for index, name in enumerate(names)}
            if data.category not in categories:
                return JSONResponse(status_code=400, content={'code': 'unknown_category', 'message': f'未知的协会类别：{data.category}'})
            return await task_service.create_index_task({'code': data.category, 'name': categories[data.category]}, data.maxPages, data.saleAfter)

        @app.get("/api/crawl-tasks")
        def list_tasks(
            status: Literal['queued', 'running', 'completed', 'partial_failed', 'failed', 'cancelled'] | None = None,
            type: Literal['association_index', 'company_documents', 'company_site_discovery'] | None = None,
            companyId: int | None = Query(default=None, gt=0), productId: int | None = Query(default=None, gt=0),
            limit: int | None = Query(default=None, ge=1, le=100), offset: int = Query(default=0, ge=0),
        ):
            items = task_service.list(status=status, task_type=type)
            if companyId is not None:
                items = [task for task in items if (task['scope'].get('company') or {}).get('id') == companyId or any(company.get('id') == companyId for company in task['scope'].get('companies', []))]
            if productId is not None:
                items = [task for task in items if any(product.get('id') == productId for product in task['scope'].get('products', []))]
            total = len(items)
            page = items[offset:offset + limit] if limit else items[offset:]
            return {'items': page, 'total': total, 'hasMore': offset + len(page) < total}

        @app.post("/api/crawl-tasks/{task_id}/retry-failed")
        async def retry_failed_task(task_id: int = Path(gt=0)):
            return await task_service.retry_failed(task_id)

        @app.post("/api/crawl-tasks/{task_id}/resume")
        async def resume_task(task_id: int = Path(gt=0)):
            return await task_service.resume(task_id)

        @app.post("/api/crawl-tasks/{task_id}/retry")
        async def retry_task(task_id: int = Path(gt=0)):
            return await task_service.retry(task_id)

        @app.post("/api/crawl-tasks/{task_id}/cancel")
        def cancel_task(task_id: int = Path(gt=0)):
            return task_service.cancel(task_id)

        @app.get("/api/crawl-tasks/{task_id}/events")
        async def task_events(task_id: int = Path(gt=0)):
            initial = await asyncio.to_thread(task_service.get, task_id)
            terminal = {'completed', 'failed', 'partial_failed', 'cancelled'}

            def event(name, data):
                return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"

            async def stream():
                detail = initial
                last_payload = json.dumps(detail, ensure_ascii=False, sort_keys=True)
                yield event('task', detail)
                while detail['status'] not in terminal:
                    await asyncio.sleep(1)
                    try:
                        detail = await asyncio.to_thread(task_service.get, task_id)
                    except Exception:
                        return
                    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True)
                    if payload != last_payload:
                        last_payload = payload
                        yield event('task', detail)
                yield event('done', {'status': detail['status']})

            return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})

        @app.get("/api/crawl-tasks/{task_id}")
        def get_task(task_id: int = Path(gt=0)):
            task = task_service.get(task_id)
            if task['type'] == 'company_site_discovery' and company_service is not None:
                companies = {company['id']: company for company in company_service.list()}
                units = {unit['unitKey']: unit for unit in task['units']}
                task['companySites'] = []
                for company in task['scope'].get('companies', []):
                    unit = units.get(f"company-site:{company['id']}", {})
                    task['companySites'].append({'companyId': company['id'], 'companyName': company['name'], 'currentSiteUrl': companies.get(company['id'], {}).get('officialSiteUrl'), 'state': unit.get('state', 'pending'), 'reason': unit.get('lastError')})
            elif task['type'] == 'association_index' and product_service is not None:
                ids = set(task_service.list_task_products(task_id))
                products = product_service.list() if ids else []
                task['products'] = [{'id': product['id'], 'name': product['confirmedName'] if product['confirmedName'] is not None else product['name'], 'companyName': product['companyName'], 'salesStatus': product['confirmedSalesStatus'] if product['confirmedSalesStatus'] is not None else product['salesStatus']} for product in products if product['id'] in ids]
            return task

    return app
