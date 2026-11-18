from asyncio import Lock
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import deadline
from app.config import Settings
from app.database import create_database
from app.keitaro import KeitaroClient, UpstreamError
from app.models import Stream
from app.schemas import (
    AddOffer,
    CampaignView,
    CreateCampaign,
    FetchInput,
    PatchOffer,
    StreamView,
    VersionInput,
)
from app.services import (
    CampaignLocks,
    add_offer,
    campaign_view,
    cancel_stream,
    create_campaign,
    patch_offer,
    publish_stream,
    require_campaign,
    require_stream,
    stream_view,
    sync_campaign,
    update_offer_catalog,
)

EntityId = Annotated[int, Path(gt=0)]


async def get_session(request: Request) -> AsyncGenerator[Session, None]:
    async with request.app.state.database_lock:
        with request.app.state.sessions() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise


DatabaseSession = Annotated[Session, Depends(get_session)]


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
    settings = settings or Settings()
    engine, sessions = create_database(settings.database_url)
    keitaro = KeitaroClient(settings, transport)
    locks = CampaignLocks()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        keitaro.close()
        engine.dispose()

    application = FastAPI(title="Adrobot API", version="1.0.0", lifespan=lifespan)
    application.state.sessions = sessions
    application.state.engine = engine
    application.state.database_lock = Lock()

    @application.middleware("http")
    async def apply_operation_deadline(request: Request, call_next):
        token = deadline.operation_deadline.set(
            deadline.monotonic() + settings.upstream_operation_timeout_seconds
        )
        try:
            return await call_next(request)
        finally:
            deadline.operation_deadline.reset(token)

    @application.exception_handler(UpstreamError)
    async def upstream_error_handler(_request: Request, error: UpstreamError):
        return JSONResponse(status_code=error.status, content={"detail": error.message})

    def stream_campaign_id(session: Session, stream_id: int) -> int:
        stream = session.get(Stream, stream_id)
        if not stream:
            raise HTTPException(404, "Stream has not been imported")
        campaign_id = stream.campaign_id
        session.rollback()
        return campaign_id

    @application.get("/api/health")
    def health():
        return {"status": "ok", "keitaro_configured": keitaro.configured}

    @application.get("/api/catalogs")
    def catalogs():
        def items(path):
            return [{"id": int(item["id"]), "name": item["name"]} for item in keitaro.get(path)]

        return {
            "domains": items("domains"),
            "groups": items("groups?type=campaigns"),
            "sources": items("traffic_sources"),
            "campaign_prefix": settings.campaign_prefix,
        }

    @application.get("/api/offers")
    def offers(
        session: DatabaseSession,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        remote = keitaro.get("offers")
        update_offer_catalog(session, remote)
        session.commit()
        query = q.strip().casefold()
        matches = [
            item
            for item in remote
            if item.get("state") != "deleted"
            and (not query or query in item["name"].casefold() or query in str(item["id"]))
        ]
        return {
            "items": [{"id": int(item["id"]), "name": item["name"]} for item in matches[:limit]],
            "has_more": len(matches) > limit,
        }

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
    def create(data: CreateCampaign, session: DatabaseSession):
        with locks.get(0):
            return campaign_view(create_campaign(session, keitaro, settings, data))

    @application.post("/api/campaigns/{campaign_id}/fetch", response_model=CampaignView)
    def fetch(campaign_id: EntityId, data: FetchInput, session: DatabaseSession):
        with locks.get(campaign_id):
            return campaign_view(
                sync_campaign(session, keitaro, settings, campaign_id, data.discard_draft)
            )

    @application.get("/api/campaigns/{campaign_id}", response_model=CampaignView)
    def campaign(campaign_id: EntityId, session: DatabaseSession):
        with locks.get(campaign_id):
            return campaign_view(require_campaign(session, campaign_id))

    @application.post("/api/streams/{stream_id}/offers", response_model=StreamView)
    def add(stream_id: EntityId, data: AddOffer, session: DatabaseSession):
        campaign_id = stream_campaign_id(session, stream_id)
        with locks.get(campaign_id):
            stream = require_stream(session, stream_id, data.version, settings)
            add_offer(session, keitaro, stream, data.offer_id)
            return stream_view(stream)

    @application.patch("/api/streams/{stream_id}/offers/{offer_id}", response_model=StreamView)
    def patch(stream_id: EntityId, offer_id: EntityId, data: PatchOffer, session: DatabaseSession):
        campaign_id = stream_campaign_id(session, stream_id)
        with locks.get(campaign_id):
            stream = require_stream(session, stream_id, data.version, settings)
            patch_offer(session, stream, offer_id, data)
            return stream_view(stream)

    @application.post("/api/streams/{stream_id}/cancel", response_model=StreamView)
    def cancel(stream_id: EntityId, data: VersionInput, session: DatabaseSession):
        campaign_id = stream_campaign_id(session, stream_id)
        with locks.get(campaign_id):
            stream = require_stream(session, stream_id, data.version, settings)
            cancel_stream(session, stream)
            return stream_view(stream)

    @application.post("/api/streams/{stream_id}/publish", response_model=StreamView)
    def publish(stream_id: EntityId, data: VersionInput, session: DatabaseSession):
        campaign_id = stream_campaign_id(session, stream_id)
        with locks.get(campaign_id):
            stream = require_stream(session, stream_id, data.version, settings)
            remote_campaign = keitaro.get(f"campaigns/{campaign_id}")
            if not remote_campaign["name"].startswith(settings.campaign_prefix):
                raise HTTPException(409, "The remote campaign is outside your test prefix")
            publish_stream(session, keitaro, stream)
            return stream_view(stream)

    return application


app = create_app()
