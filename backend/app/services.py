from copy import deepcopy
from dataclasses import dataclass
from threading import Lock, RLock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.allocation import Allocation, AllocationError, allocate_weights
from app.config import Settings
from app.database import Database
from app.errors import AppError
from app.keitaro import KeitaroClient, UpstreamError
from app.models import Campaign, Offer, Operation, Stream, StreamOffer
from app.operations import (
    find_operation,
    normalize_key,
    now,
    operation_view,
    prepare_operation,
    reject_unfinished,
    timestamp,
)
from app.schemas import CampaignView, CreateCampaign, PatchOffer, StreamView


class CampaignLocks:
    def __init__(self):
        self.guard = Lock()
        self.locks: dict[int | str, RLock] = {}

    def get(self, key: int | str) -> RLock:
        with self.guard:
            return self.locks.setdefault(key, RLock())


@dataclass(frozen=True)
class OperationResult:
    body: CampaignView | StreamView
    operation_id: str
    replayed: bool = False


def local_state(stream: Stream) -> list[dict]:
    return [
        {
            "offer_id": row.offer_id,
            "weight": row.weight,
            "pinned": row.pinned,
            "removed": row.removed,
            "position": row.position,
        }
        for row in sorted(stream.offers, key=lambda row: (row.position, row.offer_id))
    ]


def canonical_remote(offers: list[dict]) -> list[dict]:
    return sorted(
        [
            {
                "offer_id": int(row["offer_id"]),
                "share": int(row["share"]),
                "state": row.get("state", "active"),
            }
            for row in offers
        ],
        key=lambda row: row["offer_id"],
    )


def allocate(stream: Stream):
    try:
        weights = allocate_weights(
            [
                Allocation(row.offer_id, row.weight, row.pinned, row.removed, row.position)
                for row in stream.offers
            ]
        )
    except AllocationError as error:
        raise AppError(422, "INVALID_ALLOCATION", str(error)) from error
    for row in stream.offers:
        row.weight = weights[row.offer_id]
        if row.removed:
            row.pinned = False


def pending_operation(session: Session, stream: Stream):
    operation = (
        session.get(Operation, stream.pending_operation_id) if stream.pending_operation_id else None
    )
    return operation if operation and operation.status in {"pending", "uncertain"} else None


def stream_view(session: Session, stream: Stream) -> StreamView:
    rows = (
        sorted(stream.offers, key=lambda row: (row.position, row.offer_id))
        if stream.editable
        else []
    )
    names = {row.offer_id: row.offer.name for row in stream.offers}
    operation = pending_operation(session, stream)
    return StreamView(
        id=stream.id,
        campaign_id=stream.campaign_id,
        name=stream.name,
        editable=stream.editable,
        dirty=stream.editable and local_state(stream) != stream.published_state,
        version=stream.version,
        offers=[
            {
                "id": row.offer_id,
                "name": row.offer.name,
                "weight": row.weight,
                "pinned": row.pinned,
                "removed": row.removed,
            }
            for row in rows
        ],
        published_offers=[
            {
                "id": row["offer_id"],
                "name": names.get(row["offer_id"], f"Offer {row['offer_id']}"),
                "weight": row["weight"],
                "pinned": row["pinned"],
                "removed": row["removed"],
            }
            for row in stream.published_state
        ]
        if stream.editable
        else [],
        operation=operation_view(operation) if operation else None,
        total_weight=sum(row.weight for row in rows if not row.removed),
        synced_at=timestamp(stream.synced_at) if stream.synced_at else None,
    )


def campaign_view(session: Session, campaign: Campaign) -> CampaignView:
    return CampaignView(
        id=campaign.id,
        name=campaign.name,
        tracking_url=campaign.tracking_url,
        streams=[stream_view(session, stream) for stream in campaign.streams],
    )


def require_campaign(session: Session, campaign_id: int) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise AppError(404, "NOT_FOUND", "Fetch this campaign from Keitaro first")
    return campaign


def require_stream(session: Session, stream_id: int, version: int, settings: Settings):
    stream = session.get(Stream, stream_id)
    if not stream:
        raise AppError(404, "NOT_FOUND", "Stream has not been imported")
    if not stream.editable or not stream.campaign.name.startswith(settings.campaign_prefix):
        raise AppError(
            422, "STREAM_NOT_EDITABLE", "Only offer streams in your test campaigns can be edited"
        )
    operation = pending_operation(session, stream)
    if operation:
        raise AppError(
            409,
            "OPERATION_PENDING",
            "Reconcile the pending publication before editing this stream",
            operation.id,
            "unknown",
        )
    if stream.version != version:
        raise AppError(
            409,
            "STALE_VERSION",
            "This stream changed in another tab; reload the campaign",
            outcome="not_applied",
        )
    return stream


def update_offer_catalog(session: Session, rows: list[dict]):
    for item in rows:
        statement = insert(Offer).values(id=int(item["id"]), name=item["name"])
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[Offer.id], set_={"name": statement.excluded.name}
            )
        )
        session.get(Offer, int(item["id"])).name = item["name"]
    session.flush()


def apply_remote_snapshot(session: Session, campaign_id: int, bundle: dict):
    remote_campaign, remote_streams = bundle["campaign"], bundle["streams"]
    update_offer_catalog(session, bundle["offers"])
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        campaign = Campaign(id=campaign_id, name=remote_campaign["name"])
        session.add(campaign)
    campaign.name = remote_campaign["name"]
    domain = remote_campaign.get("domain")
    if isinstance(domain, dict):
        domain = domain.get("name")
    alias = remote_campaign.get("alias")
    campaign.tracking_url = None
    if domain and alias:
        domain = domain if domain.startswith(("https://", "http://")) else "https://" + domain
        campaign.tracking_url = domain.rstrip("/") + "/" + alias
    session.flush()
    by_id = {stream.id: stream for stream in campaign.streams}
    seen = set()
    for remote in remote_streams:
        stream_id = int(remote["id"])
        seen.add(stream_id)
        stream = by_id.get(stream_id)
        if not stream:
            stream = Stream(
                id=stream_id,
                campaign=campaign,
                name=remote["name"],
                position=0,
                version=0,
                offers=[],
            )
            session.add(stream)
        stream.name = remote["name"]
        stream.position = int(remote.get("position", 0))
        stream.editable = (
            remote.get("schema") == "landings"
            and remote.get("state") != "deleted"
            and bundle["writable"]
        )
        stream.version += 1
        stream.synced_at = now()
        stream.remote_offers = canonical_remote(remote.get("offers", []))
        old_rows = {row.offer_id: row for row in stream.offers}
        remote_ids = set()
        next_position = max((row.position for row in stream.offers), default=-1) + 1
        for source in remote.get("offers", []):
            item = canonical_remote([source])[0]
            offer_id = item["offer_id"]
            remote_ids.add(offer_id)
            offer = session.get(Offer, offer_id)
            row = old_rows.get(offer_id)
            if not row:
                row = StreamOffer(
                    stream=stream,
                    offer=offer,
                    offer_id=offer_id,
                    pinned=False,
                    position=next_position,
                )
                session.add(row)
                next_position += 1
            elif row.removed or row.weight != item["share"]:
                row.pinned = False
            row.removed = item["state"] != "active"
            row.weight = 0 if row.removed else item["share"]
        for row in stream.offers:
            if row.offer_id not in remote_ids:
                row.removed, row.pinned, row.weight = True, False, 0
        session.flush()
        stream.published_state = local_state(stream)
    for stream in campaign.streams:
        if stream.id not in seen:
            stream.editable = False
            stream.version += 1
            stream.published_state = local_state(stream)
    session.flush()
    session.expire(campaign, ["streams"])
    return campaign


class CampaignService:
    def __init__(self, database: Database, client: KeitaroClient, settings: Settings):
        self.db, self.client, self.settings = database, client, settings
        self.target = {
            "mode": settings.keitaro_mode,
            "base_url": "local-demo"
            if settings.keitaro_mode == "demo"
            else settings.keitaro_base_url,
        }
        self.db.target = self.target
        self.campaign_locks = CampaignLocks()
        self.operation_locks = CampaignLocks()

    def get_campaign(self, campaign_id: int):
        with self.db.transaction() as session:
            return campaign_view(session, require_campaign(session, campaign_id))

    def campaign_id(self, stream_id: int):
        with self.db.transaction() as session:
            stream = session.get(Stream, stream_id)
            if not stream:
                raise AppError(404, "NOT_FOUND", "Stream has not been imported")
            return stream.campaign_id

    def offers(self, query: str, limit: int):
        remote = self.client.get("offers")
        with self.db.transaction() as session:
            update_offer_catalog(session, remote)
        query = query.strip().casefold()
        matches = [
            row
            for row in remote
            if row.get("state") != "deleted"
            and (not query or query in row["name"].casefold() or query in str(row["id"]))
        ]
        return {
            "items": [{"id": int(row["id"]), "name": row["name"]} for row in matches[:limit]],
            "has_more": len(matches) > limit,
        }

    def _bundle(self, campaign_id: int):
        remote_campaign = self.client.get(f"campaigns/{campaign_id}")
        remote_streams = self.client.get(f"campaigns/{campaign_id}/streams")
        offers = self.client.get("offers")
        known = {int(offer["id"]) for offer in offers}
        with self.db.transaction() as session:
            saved_names = {row.id: row.name for row in session.scalars(select(Offer))}
        for stream in remote_streams:
            for item in stream.get("offers", []):
                offer_id = int(item["offer_id"])
                if offer_id not in known:
                    offer = (
                        {"id": offer_id, "name": saved_names[offer_id]}
                        if offer_id in saved_names
                        else self.client.get(f"offers/{offer_id}")
                    )
                    offers.append(offer)
                    known.add(offer_id)
        return {
            "campaign": remote_campaign,
            "streams": remote_streams,
            "offers": offers,
            "writable": remote_campaign["name"].startswith(self.settings.campaign_prefix),
        }

    def _guard_sync(self, session: Session, campaign_id: int, discard_draft: bool):
        existing = session.get(Campaign, campaign_id)
        if not existing:
            return
        for stream in existing.streams:
            operation = pending_operation(session, stream)
            if operation:
                raise AppError(
                    409,
                    "OPERATION_PENDING",
                    "Reconcile the pending publication before synchronizing",
                    operation.id,
                    "unknown",
                )
            if not discard_draft and local_state(stream) != stream.published_state:
                raise AppError(
                    409,
                    "DRAFT_CONFLICT",
                    "This campaign has a local draft; cancel it or confirm discard",
                    outcome="not_applied",
                )

    def sync_campaign(self, campaign_id: int, discard_draft: bool):
        with self.campaign_locks.get(campaign_id):
            with self.db.transaction() as session:
                self._guard_sync(session, campaign_id, discard_draft)
            bundle = self._bundle(campaign_id)
            with self.db.transaction() as session:
                self._guard_sync(session, campaign_id, discard_draft)
                return campaign_view(session, apply_remote_snapshot(session, campaign_id, bundle))

    def add_offer(self, stream_id: int, version: int, offer_id: int):
        with self.campaign_locks.get(self.campaign_id(stream_id)):
            with self.db.transaction() as session:
                stream = require_stream(session, stream_id, version, self.settings)
                existing = next((row for row in stream.offers if row.offer_id == offer_id), None)
                if existing and not existing.removed:
                    raise AppError(
                        422, "OFFER_ALREADY_ACTIVE", "This offer is already active in the stream"
                    )
            remote = self.client.get(f"offers/{offer_id}")
            if remote.get("state") == "deleted":
                raise AppError(
                    422, "OFFER_UNAVAILABLE", "An archived Keitaro offer cannot be added"
                )
            with self.db.transaction() as session:
                stream = require_stream(session, stream_id, version, self.settings)
                update_offer_catalog(session, [remote])
                existing = next((row for row in stream.offers if row.offer_id == offer_id), None)
                if existing:
                    existing.removed, existing.pinned = False, False
                else:
                    session.add(
                        StreamOffer(
                            stream=stream,
                            offer=session.get(Offer, offer_id),
                            offer_id=offer_id,
                            position=max((row.position for row in stream.offers), default=-1) + 1,
                            weight=0,
                            pinned=False,
                            removed=False,
                        )
                    )
                allocate(stream)
                stream.version += 1
                return stream_view(session, stream)

    def patch_offer(self, stream_id: int, offer_id: int, changes: PatchOffer):
        with self.campaign_locks.get(self.campaign_id(stream_id)):
            with self.db.transaction() as session:
                stream = require_stream(session, stream_id, changes.version, self.settings)
                row = next((row for row in stream.offers if row.offer_id == offer_id), None)
                if not row:
                    raise AppError(404, "NOT_FOUND", "Offer is not part of this stream")
                if changes.removed is not None:
                    was_removed = row.removed
                    row.removed = changes.removed
                    if changes.removed or was_removed:
                        row.pinned = False
                if row.removed and (changes.pinned is True or changes.weight is not None):
                    raise AppError(
                        422, "OFFER_REMOVED", "Restore the offer before assigning a weight"
                    )
                if changes.pinned is not None:
                    row.pinned = changes.pinned
                if changes.weight is not None:
                    row.weight, row.pinned = changes.weight, True
                allocate(stream)
                stream.version += 1
                return stream_view(session, stream)

    def cancel_stream(self, stream_id: int, version: int):
        with self.campaign_locks.get(self.campaign_id(stream_id)):
            with self.db.transaction() as session:
                stream = require_stream(session, stream_id, version, self.settings)
                baseline = {row["offer_id"]: row for row in stream.published_state}
                for row in list(stream.offers):
                    item = baseline.get(row.offer_id)
                    if not item:
                        stream.offers.remove(row)
                        session.delete(row)
                    else:
                        for key in ("weight", "pinned", "removed", "position"):
                            setattr(row, key, item[key])
                stream.version += 1
                return stream_view(session, stream)

    def _operation(self, operation_id: str):
        with self.db.transaction() as session:
            operation = session.get(Operation, operation_id)
            if not operation:
                raise AppError(404, "NOT_FOUND", "Operation was not found")
            if operation.context_json.get("target") != self.target:
                raise AppError(
                    409,
                    "OPERATION_TARGET_MISMATCH",
                    "This operation belongs to another Keitaro target",
                    operation.id,
                    "not_applied",
                )
            return {
                "id": operation.id,
                "kind": operation.kind,
                "key": operation.idempotency_key,
                "status": operation.status,
                "phase": operation.phase,
                "campaign_id": operation.campaign_id,
                "stream_id": operation.stream_id,
                "context": deepcopy(operation.context_json),
            }

    def operation(self, operation_id: str):
        with self.db.transaction() as session:
            operation = session.get(Operation, operation_id)
            if not operation:
                raise AppError(404, "NOT_FOUND", "Operation was not found")
            return operation_view(operation)

    def operations(self, limit: int):
        with self.db.transaction() as session:
            rows = session.scalars(
                select(Operation)
                .order_by(Operation.created_at.desc(), Operation.id.desc())
                .limit(limit)
            )
            return {"items": [operation_view(row) for row in rows]}

    def _phase(self, operation_id: str, phase: str, **changes):
        with self.db.transaction() as session:
            operation = session.get(Operation, operation_id)
            operation.phase = phase
            operation.updated_at = now()
            for key, value in changes.items():
                setattr(operation, key, value)

    def _record_error(self, operation_id: str, error: AppError, status: str):
        with self.db.transaction() as session:
            operation = session.get(Operation, operation_id)
            if operation.status == "succeeded":
                return
            operation.status, operation.detail = status, error.message
            operation.error_code, operation.error_status = error.code, error.status
            operation.updated_at = now()
            if status in {"failed", "conflict"} and operation.stream_id:
                stream = session.get(Stream, operation.stream_id)
                if stream and stream.pending_operation_id == operation_id:
                    stream.pending_operation_id = None

    def _operation_error(self, operation_id: str, error: Exception, status: str):
        if isinstance(error, AppError):
            wrapped = AppError(
                error.status,
                error.code,
                error.message,
                operation_id,
                "unknown" if status in {"uncertain", "conflict"} else "not_applied",
            )
        else:
            wrapped = AppError(
                500,
                "LOCAL_PERSISTENCE_ERROR",
                "The remote result needs reconciliation after a local failure",
                operation_id,
                "unknown",
            )
            status = "uncertain"
        try:
            self._record_error(operation_id, wrapped, status)
        except Exception:
            pass
        return wrapped

    def create_campaign(self, data: CreateCampaign, idempotency_key: str | None):
        if not data.name.startswith(self.settings.campaign_prefix):
            raise AppError(
                422,
                "INVALID_CAMPAIGN_PREFIX",
                f"Campaign name must start with {self.settings.campaign_prefix}",
            )
        key = normalize_key(idempotency_key)
        payload = {
            **data.model_dump(),
            "target": self.target,
            "campaign_prefix": self.settings.campaign_prefix,
        }
        with self.operation_locks.get("create:" + key):
            with self.db.transaction() as session:
                existing = find_operation(session, "create_campaign", key, payload, self.target)
                if existing:
                    if existing.status == "succeeded":
                        return OperationResult(
                            CampaignView.model_validate(existing.result_json), existing.id, True
                        )
                    reject_unfinished(existing)
                operation = prepare_operation(
                    session,
                    "create_campaign",
                    key,
                    payload,
                    {"request": data.model_dump(), "alias": uuid4().hex, "target": self.target},
                )
                operation_id = operation.id
            return self._execute_create(operation_id)

    def _execute_create(self, operation_id: str):
        operation = self._operation(operation_id)
        request, alias = operation["context"]["request"], operation["context"]["alias"]
        campaign_id = None
        phase = "validating"
        try:
            self._phase(operation_id, phase)
            for path, value, label in (
                ("domains", request["domain_id"], "domain"),
                ("groups?type=campaigns", request["group_id"], "group"),
                ("traffic_sources", request["source_id"], "source"),
            ):
                if value not in {int(item["id"]) for item in self.client.get(path)}:
                    raise AppError(
                        422, "INVALID_CATALOG_REFERENCE", f"Choose an available Keitaro {label}"
                    )
            offer = self.client.get(f"offers/{request['offer_id']}")
            if offer.get("state") == "deleted":
                raise AppError(422, "OFFER_UNAVAILABLE", "Choose an active Keitaro offer")
            phase = "creating_campaign"
            self._phase(operation_id, phase)
            remote = self.client.post(
                "campaigns",
                {
                    "name": request["name"],
                    "alias": alias,
                    "type": "position",
                    "state": "active",
                    "domain_id": request["domain_id"],
                    "group_id": request["group_id"],
                    "traffic_source_id": request["source_id"],
                },
            )
            campaign_id = int(remote["id"])
            self._phase(operation_id, "campaign_created", campaign_id=campaign_id)
            first, second = self._expected_streams(request, campaign_id)
            phase = "creating_redirect"
            self._phase(operation_id, phase)
            self.client.post("streams", first)
            phase = "creating_offers"
            self._phase(operation_id, phase)
            self.client.post("streams", second)
            phase = "importing"
            self._phase(operation_id, phase)
            with self.campaign_locks.get(campaign_id):
                bundle = self._bundle(campaign_id)
                self._verify_creation(operation, bundle)
                return self._complete_create(operation_id, campaign_id, bundle)
        except Exception as error:
            if (
                isinstance(error, UpstreamError)
                and not error.ambiguous
                and campaign_id
                and phase in {"creating_redirect", "creating_offers"}
            ):
                try:
                    self._phase(operation_id, "rolling_back")
                    self.client.delete(f"campaigns/{campaign_id}")
                except Exception:
                    uncertain = AppError(
                        502,
                        "CREATION_ROLLBACK_UNCERTAIN",
                        f"Campaign {campaign_id} could not be completed or archived; inspect the operation",
                        outcome="unknown",
                    )
                    raise self._operation_error(operation_id, uncertain, "uncertain") from error
                failed = AppError(
                    error.status,
                    error.code,
                    f"Campaign setup failed; newly created campaign {campaign_id} was archived",
                )
                raise self._operation_error(operation_id, failed, "failed") from error
            uncertain = (
                (isinstance(error, UpstreamError) and error.ambiguous)
                or phase == "importing"
                or not isinstance(error, AppError)
            )
            if uncertain and campaign_id and isinstance(error, AppError):
                error = AppError(
                    error.status,
                    error.code,
                    f"Campaign {campaign_id} has an uncertain result; reconcile the operation",
                    outcome="unknown",
                )
            raise self._operation_error(
                operation_id, error, "uncertain" if uncertain else "failed"
            ) from error

    @staticmethod
    def _expected_streams(request: dict, campaign_id: int):
        common = {
            "campaign_id": campaign_id,
            "state": "active",
            "action_type": "http",
            "collect_clicks": True,
        }
        return (
            {
                **common,
                "name": f"Country {request['country_code']} → Google",
                "type": "regular",
                "schema": "redirect",
                "position": 1,
                "action_payload": "https://google.com",
                "filters": [
                    {"name": "country", "mode": "accept", "payload": [request["country_code"]]}
                ],
            },
            {
                **common,
                "name": "Offers",
                "type": "default",
                "schema": "landings",
                "position": 2,
                "landings": [],
                "offers": [{"offer_id": request["offer_id"], "share": 100, "state": "active"}],
            },
        )

    def _verify_creation(self, operation: dict, bundle: dict):
        request, campaign = operation["context"]["request"], bundle["campaign"]
        if campaign.get("alias") != operation["context"]["alias"] or any(
            campaign.get(field) != expected
            for field, expected in (
                ("name", request["name"]),
                ("domain_id", request["domain_id"]),
                ("group_id", request["group_id"]),
                ("traffic_source_id", request["source_id"]),
                ("state", "active"),
                ("type", "position"),
            )
        ):
            raise AppError(
                409,
                "REMOTE_CONFLICT",
                "The created campaign settings differ from the saved operation",
                outcome="unknown",
            )
        expected = self._expected_streams(request, int(campaign["id"]))
        streams = sorted(bundle["streams"], key=lambda row: int(row.get("position", 0)))
        if len(streams) != 2:
            raise AppError(
                409,
                "CREATION_INCOMPLETE",
                "Campaign exists but the two required streams are not confirmed",
                outcome="unknown",
            )
        for actual, target in zip(streams, expected, strict=True):
            if any(
                actual.get(field) != target[field]
                for field in ("type", "schema", "position", "state")
            ):
                raise AppError(
                    409,
                    "CREATION_INCOMPLETE",
                    "Campaign stream routing does not match the saved operation",
                    outcome="unknown",
                )
        filters = [
            {key: item.get(key) for key in ("name", "mode", "payload")}
            for item in streams[0].get("filters", [])
        ]
        if (
            streams[0].get("action_type") != "http"
            or streams[0].get("action_payload") != "https://google.com"
            or filters != expected[0]["filters"]
            or streams[1].get("landings", []) != []
            or canonical_remote(streams[1].get("offers", [])) != expected[1]["offers"]
        ):
            raise AppError(
                409,
                "CREATION_INCOMPLETE",
                "Campaign routing or offer weights could not be confirmed",
                outcome="unknown",
            )

    def _complete_create(self, operation_id: str, campaign_id: int, bundle: dict):
        with self.campaign_locks.get(campaign_id):
            with self.db.transaction() as session:
                operation = session.get(Operation, operation_id)
                if operation.status == "succeeded":
                    return OperationResult(
                        CampaignView.model_validate(operation.result_json), operation_id, True
                    )
                campaign = session.get(Campaign, campaign_id)
                protected = campaign and any(
                    local_state(stream) != stream.published_state
                    or pending_operation(session, stream)
                    for stream in campaign.streams
                )
                if not protected:
                    campaign = apply_remote_snapshot(session, campaign_id, bundle)
                operation.status, operation.phase = "succeeded", "completed"
                operation.campaign_id = campaign_id
                operation.detail, operation.error_code, operation.error_status = None, None, None
                operation.updated_at = now()
                body = campaign_view(session, campaign)
                operation.result_json = body.model_dump(mode="json")
                return OperationResult(body, operation_id)

    def publish_stream(self, stream_id: int, version: int, idempotency_key: str | None):
        key = normalize_key(idempotency_key)
        payload = {
            "stream_id": stream_id,
            "version": version,
            "target": self.target,
            "campaign_prefix": self.settings.campaign_prefix,
        }
        with self.operation_locks.get("publish:" + key):
            with self.db.transaction() as session:
                existing = find_operation(session, "publish_stream", key, payload, self.target)
                if existing:
                    if existing.status == "succeeded":
                        return OperationResult(
                            StreamView.model_validate(existing.result_json), existing.id, True
                        )
                    reject_unfinished(existing)
            with self.campaign_locks.get(self.campaign_id(stream_id)):
                with self.db.transaction() as session:
                    stream = require_stream(session, stream_id, version, self.settings)
                    allocate(stream)
                    snapshot = local_state(stream)
                    target = [
                        {"offer_id": row.offer_id, "share": row.weight, "state": "active"}
                        for row in sorted(
                            stream.offers, key=lambda row: (row.position, row.offer_id)
                        )
                        if not row.removed
                    ]
                    active_ids = {row["offer_id"] for row in target}
                    target.extend(
                        row
                        for row in stream.remote_offers
                        if row["state"] == "disabled" and row["offer_id"] not in active_ids
                    )
                    operation = prepare_operation(
                        session,
                        "publish_stream",
                        key,
                        payload,
                        {
                            "baseline": deepcopy(stream.remote_offers),
                            "offers_target": canonical_remote(target),
                            "snapshot": snapshot,
                            "version": version,
                            "target": self.target,
                        },
                        campaign_id=stream.campaign_id,
                        stream_id=stream_id,
                    )
                    operation_id = operation.id
                    stream.pending_operation_id = operation_id
                try:
                    operation = self._operation(operation_id)
                    remote = self._publication_remote(operation)
                    actual = canonical_remote(remote.get("offers", []))
                    if actual == operation["context"]["offers_target"]:
                        return self._complete_publish(operation_id)
                    if actual != operation["context"]["baseline"]:
                        raise AppError(
                            409,
                            "REMOTE_CONFLICT",
                            "The stream changed in Keitaro; fetch it again before publishing",
                            outcome="unknown",
                        )
                    self._phase(operation_id, "publishing")
                    published = self.client.put(
                        f"streams/{stream_id}", {"offers": operation["context"]["offers_target"]}
                    )
                    if (
                        canonical_remote(published.get("offers", []))
                        != operation["context"]["offers_target"]
                    ):
                        raise AppError(
                            502,
                            "UPSTREAM_INVALID_RESPONSE",
                            "Keitaro returned unexpected offer weights; reconcile the operation",
                            outcome="unknown",
                        )
                    return self._complete_publish(operation_id)
                except Exception as error:
                    status = (
                        "conflict"
                        if isinstance(error, AppError)
                        and error.code in {"REMOTE_CONFLICT", "LOCAL_STATE_CONFLICT"}
                        else "uncertain"
                        if not isinstance(error, AppError) or error.outcome == "unknown"
                        else "failed"
                    )
                    raise self._operation_error(operation_id, error, status) from error

    def _publication_remote(self, operation: dict):
        remote_campaign = self.client.get(f"campaigns/{operation['campaign_id']}")
        if not remote_campaign["name"].startswith(self.settings.campaign_prefix):
            raise AppError(
                409,
                "REMOTE_CONFLICT",
                "The remote campaign is outside your test prefix",
                outcome="unknown",
            )
        remote = self.client.get(f"streams/{operation['stream_id']}")
        if (
            int(remote.get("campaign_id", -1)) != operation["campaign_id"]
            or remote.get("schema") != "landings"
            or remote.get("state") == "deleted"
        ):
            raise AppError(
                409, "REMOTE_CONFLICT", "The remote stream is no longer editable", outcome="unknown"
            )
        return remote

    def _complete_publish(self, operation_id: str):
        with self.db.transaction() as session:
            operation = session.get(Operation, operation_id)
            if operation.status == "succeeded":
                return OperationResult(
                    StreamView.model_validate(operation.result_json), operation_id, True
                )
            stream = session.get(Stream, operation.stream_id)
            context = operation.context_json
            if (
                not stream
                or stream.pending_operation_id != operation_id
                or stream.version != context["version"]
                or local_state(stream) != context["snapshot"]
            ):
                raise AppError(
                    409,
                    "LOCAL_STATE_CONFLICT",
                    "The saved publication snapshot no longer matches local state",
                    operation_id,
                    "unknown",
                )
            stream.remote_offers = deepcopy(context["offers_target"])
            stream.published_state = deepcopy(context["snapshot"])
            stream.version += 1
            stream.synced_at, stream.pending_operation_id = now(), None
            operation.status, operation.phase = "succeeded", "completed"
            operation.detail, operation.error_code, operation.error_status = None, None, None
            operation.updated_at = now()
            body = stream_view(session, stream)
            operation.result_json = body.model_dump(mode="json")
            return OperationResult(body, operation_id)

    def reconcile(self, operation_id: str):
        operation = self._operation(operation_id)
        lock_key = (
            "create:" if operation["kind"] == "create_campaign" else "publish:"
        ) + operation["key"]
        with self.operation_locks.get(lock_key):
            operation = self._operation(operation_id)
            if operation["status"] not in {"pending", "uncertain"}:
                return self.operation(operation_id)
            try:
                if operation["kind"] == "publish_stream":
                    with self.campaign_locks.get(operation["campaign_id"]):
                        remote = self._publication_remote(operation)
                        actual = canonical_remote(remote.get("offers", []))
                        if actual == operation["context"]["offers_target"]:
                            self._complete_publish(operation_id)
                        elif actual == operation["context"]["baseline"]:
                            self._record_error(
                                operation_id,
                                AppError(
                                    409,
                                    "PUBLICATION_UNCONFIRMED",
                                    "Keitaro still shows the original offers; the write is not confirmed",
                                    operation_id,
                                    "unknown",
                                ),
                                "uncertain",
                            )
                        else:
                            self._record_error(
                                operation_id,
                                AppError(
                                    409,
                                    "REMOTE_CONFLICT",
                                    "Keitaro contains a different offer configuration; the saved draft was preserved",
                                    operation_id,
                                    "unknown",
                                ),
                                "conflict",
                            )
                else:
                    self._reconcile_creation(operation)
            except Exception as error:
                status = (
                    "conflict"
                    if isinstance(error, AppError)
                    and error.code in {"REMOTE_CONFLICT", "LOCAL_STATE_CONFLICT"}
                    else "uncertain"
                )
                self._operation_error(operation_id, error, status)
            return self.operation(operation_id)

    def _reconcile_creation(self, operation: dict):
        alias = operation["context"]["alias"]
        matches = [row for row in self.client.get("campaigns") if row.get("alias") == alias]
        if len(matches) > 1:
            raise AppError(
                409,
                "REMOTE_CONFLICT",
                "Multiple campaigns match the saved alias; manual inspection is required",
                outcome="unknown",
            )
        if not matches:
            raise AppError(
                409,
                "CREATION_UNCONFIRMED",
                "The saved alias is not visible in Keitaro; no write was repeated",
                outcome="unknown",
            )
        campaign = matches[0]
        campaign_id = int(campaign["id"])
        self._phase(operation["id"], "reconciling", campaign_id=campaign_id)
        if campaign.get("state") == "deleted":
            self._record_error(
                operation["id"],
                AppError(
                    409,
                    "CREATION_ARCHIVED",
                    f"Campaign {campaign_id} is archived",
                    operation["id"],
                    "not_applied",
                ),
                "failed",
            )
            return
        with self.campaign_locks.get(campaign_id):
            bundle = self._bundle(campaign_id)
            self._verify_creation(operation, bundle)
            self._complete_create(operation["id"], campaign_id, bundle)
