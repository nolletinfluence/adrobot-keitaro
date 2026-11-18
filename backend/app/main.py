from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from starlette.exceptions import HTTPException

from app import deadline
from app.config import Settings
from app.database import Database
from app.errors import AppError
from app.keitaro import KeitaroClient
from app.observability import error_response, request_context
from app.schemas import (
    AddOffer,
    CampaignView,
    CreateCampaign,
    DemoFault,
    FetchInput,
    OperationView,
    PatchOffer,
    StreamView,
    VersionInput,
)
from app.services import CampaignService, OperationResult

EntityId = Annotated[int, Path(gt=0)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
    settings = settings or Settings()
    database = Database(settings.database_url)
    client_settings = settings
    demo = None
    if settings.keitaro_mode == "demo":
        client_settings = settings.model_copy(update={"keitaro_api_key": SecretStr("test-key")})
        if transport is None:
            from app.demo import DemoKeitaro

            filename = make_url(settings.database_url).database
            database_path = (
                FilePath(filename)
                if filename and filename != ":memory:"
                else FilePath("data/adrobot.db")
            )
            state_path = database_path.expanduser().resolve().with_name("keitaro-demo.json")
            demo = DemoKeitaro(state_path, settings.campaign_prefix)
            transport = httpx.MockTransport(demo)
    keitaro = KeitaroClient(client_settings, transport)
    service = CampaignService(database, keitaro, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        keitaro.close()
        database.engine.dispose()

    application = FastAPI(title="Adrobot API", version="1.1.0", lifespan=lifespan)
    application.state.sessions = database.sessions
    application.state.engine = database.engine
    application.state.database = database
    application.state.service = service
    application.state.demo = demo
    application.middleware("http")(request_context)

    @application.middleware("http")
    async def apply_operation_deadline(request: Request, call_next):
        token = deadline.operation_deadline.set(
            deadline.monotonic() + settings.upstream_operation_timeout_seconds
        )
        try:
            return await call_next(request)
        finally:
            deadline.operation_deadline.reset(token)

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError):
        return error_response(request, error)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException):
        return error_response(request, AppError(error.status_code, "HTTP_ERROR", str(error.detail)))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        request.state.error_code = "VALIDATION_ERROR"
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()
                ],
                "code": "VALIDATION_ERROR",
                "operation_id": None,
                "outcome": "not_applied",
                "request_id": request.state.request_id,
            },
            headers={"X-Request-ID": request.state.request_id},
        )

    def operation_response(response: Response, result: OperationResult):
        response.headers["X-Operation-ID"] = result.operation_id
        if result.replayed:
            response.headers["Idempotent-Replay"] = "true"
        return result.body

    @application.get("/api/health")
    def health():
        return {
            "status": "ok",
            "keitaro_configured": keitaro.configured,
            "mode": settings.keitaro_mode,
        }

    @application.post("/api/demo/fault")
    def demo_fault(data: DemoFault):
        if settings.keitaro_mode != "demo" or demo is None:
            raise AppError(404, "NOT_FOUND", "Demo scenarios are unavailable in live mode")
        demo.arm_fault(data.kind)
        return {"armed": data.kind}

    @application.get("/api/catalogs")
    def catalogs():
        def items(path):
            return [{"id": int(item["id"]), "name": item["name"]} for item in keitaro.get(path)]

        return {
            "domains": items("domains"),
            "groups": items("groups?type=campaigns"),
            "sources": items("traffic_sources"),
            "campaign_prefix": settings.campaign_prefix,
            "mode": settings.keitaro_mode,
        }

    @application.get("/api/offers")
    def offers(
        q: str = Query(default="", max_length=200), limit: int = Query(default=20, ge=1, le=100)
    ):
        return service.offers(q, limit)

    @application.get("/api/campaigns")
    def campaigns(q: str = Query(default="", max_length=200)):
        query = q.strip().casefold()
        rows = keitaro.get("campaigns")
        return {
            "items": [
                {"id": int(item["id"]), "name": item["name"]}
                for item in rows
                if item.get("state") != "deleted"
                and item["name"].startswith(settings.campaign_prefix)
                and (not query or query in item["name"].casefold() or query in str(item["id"]))
            ]
        }

    @application.post("/api/campaigns", response_model=CampaignView, status_code=201)
    def create(data: CreateCampaign, response: Response, idempotency_key: IdempotencyKey = None):
        return operation_response(response, service.create_campaign(data, idempotency_key))

    @application.post("/api/campaigns/{campaign_id}/fetch", response_model=CampaignView)
    def fetch(campaign_id: EntityId, data: FetchInput):
        return service.sync_campaign(campaign_id, data.discard_draft)

    @application.get("/api/campaigns/{campaign_id}", response_model=CampaignView)
    def campaign(campaign_id: EntityId):
        return service.get_campaign(campaign_id)

    @application.post("/api/streams/{stream_id}/offers", response_model=StreamView)
    def add(stream_id: EntityId, data: AddOffer):
        return service.add_offer(stream_id, data.version, data.offer_id)

    @application.patch("/api/streams/{stream_id}/offers/{offer_id}", response_model=StreamView)
    def patch(stream_id: EntityId, offer_id: EntityId, data: PatchOffer):
        return service.patch_offer(stream_id, offer_id, data)

    @application.post("/api/streams/{stream_id}/cancel", response_model=StreamView)
    def cancel(stream_id: EntityId, data: VersionInput):
        return service.cancel_stream(stream_id, data.version)

    @application.post("/api/streams/{stream_id}/publish", response_model=StreamView)
    def publish(
        stream_id: EntityId,
        data: VersionInput,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ):
        return operation_response(
            response, service.publish_stream(stream_id, data.version, idempotency_key)
        )

    @application.get("/api/operations")
    def operations(limit: int = Query(default=20, ge=1, le=100)):
        return service.operations(limit)

    @application.get("/api/operations/{operation_id}", response_model=OperationView)
    def operation(operation_id: str):
        return service.operation(operation_id)

    @application.post("/api/operations/{operation_id}/reconcile", response_model=OperationView)
    def reconcile(operation_id: str, response: Response):
        response.headers["X-Operation-ID"] = operation_id
        return service.reconcile(operation_id)

    return application


app = create_app()
