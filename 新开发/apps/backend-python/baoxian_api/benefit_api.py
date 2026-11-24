import os
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .benefit_illustration import (
    BenefitDataMissingError,
    BenefitIllustrationService,
    ParameterOutOfRangeError,
    UnsupportedProductTemplateError,
)
from .documents import DocumentService, LayoutStorageError
from .products import ProductNotFoundError
from .storage import MinioStorage


class BenefitIllustrationInput(BaseModel):
    productId: int = Field(gt=0)
    age: int = Field(strict=True, ge=0, le=75)
    gender: Literal['male', 'female']
    coveragePeriod: Literal['to_60', 'to_70', 'to_80', 'to_90']
    paymentPeriod: Literal['single', '3', '5', '10']
    annualPremium: float = Field(gt=0, le=10000000)


def create_benefit_app(*, internal_api_token: str, benefit_service: BenefitIllustrationService) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=400, content={"code": "validation_error", "message": "请求参数无效", "details": [{"path": ".".join(str(part) for part in issue["loc"] if part not in ("body", "path", "query")), "message": issue["msg"]} for issue in error.errors()]})

    @app.exception_handler(ProductNotFoundError)
    async def product_not_found_handler(request: Request, error: ProductNotFoundError):
        return JSONResponse(status_code=404, content={"code": "not_found", "message": f"产品不存在：{error}"})

    @app.exception_handler(BenefitDataMissingError)
    async def data_missing_handler(request: Request, error: BenefitDataMissingError):
        return JSONResponse(status_code=409, content={"code": "benefit_data_missing", "message": str(error)})

    @app.exception_handler(ParameterOutOfRangeError)
    async def out_of_range_handler(request: Request, error: ParameterOutOfRangeError):
        return JSONResponse(status_code=422, content={"code": "parameter_out_of_range", "message": str(error)})

    @app.exception_handler(UnsupportedProductTemplateError)
    async def unsupported_template_handler(request: Request, error: UnsupportedProductTemplateError):
        return JSONResponse(status_code=422, content={"code": "unsupported_product_template", "message": str(error)})

    @app.exception_handler(LayoutStorageError)
    async def layout_storage_handler(request: Request, error: LayoutStorageError):
        return JSONResponse(status_code=503, content={"code": "layout_storage_unavailable", "message": str(error)})

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path != "/health":
            if request.headers.get("authorization") != f"Bearer {internal_api_token}":
                return JSONResponse(status_code=401, content={"code": "unauthorized", "message": "缺少或无效的内部访问令牌"})
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "baoxian-benefit-api"}

    @app.post("/api/benefit-illustrations")
    async def benefit_illustration(data: BenefitIllustrationInput):
        return await benefit_service.generate(
            product_id=data.productId,
            age=data.age,
            gender=data.gender,
            coverage=data.coveragePeriod,
            payment=data.paymentPeriod,
            annual_premium=data.annualPremium,
        )

    return app


def build_from_env(env):
    token = env.get("INTERNAL_API_TOKEN", "").strip()
    database_url = env.get("DATABASE_URL", "").strip()
    crawl_database_url = env.get("CRAWL_DATABASE_URL", "").strip()
    if not token:
        raise ValueError("缺少 INTERNAL_API_TOKEN")
    if not database_url:
        raise ValueError("缺少 DATABASE_URL")
    storage = MinioStorage(env.get('MINIO_ENDPOINT', '127.0.0.1'), int(env.get('MINIO_PORT', '9000')), env.get('MINIO_USE_SSL', 'false') == 'true', env.get('MINIO_ACCESS_KEY', 'baoxian'), env.get('MINIO_SECRET_KEY', 'baoxian_minio_dev'), env.get('MINIO_BUCKET', 'baoxian-documents'))
    documents = DocumentService(database_url, crawl_database_url or database_url, storage)
    return create_benefit_app(internal_api_token=token, benefit_service=BenefitIllustrationService(documents))


def create_application():
    return build_from_env(os.environ)
