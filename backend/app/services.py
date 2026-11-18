from datetime import datetime, timezone
from threading import Lock, RLock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro import KeitaroClient, UpstreamError
from app.models import Campaign, Offer, Stream, StreamOffer
from app.schemas import CampaignView, CreateCampaign, PatchOffer, StreamView


class CampaignLocks:
    def __init__(self):
        self.guard = Lock()
        self.locks: dict[int, RLock] = {}

    def get(self, campaign_id: int) -> RLock:
        with self.guard:
            return self.locks.setdefault(campaign_id, RLock())


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
    return [
        {
            "offer_id": int(row["offer_id"]),
            "share": int(row["share"]),
            "state": row.get("state", "active"),
        }
        for row in offers
    ]


def allocate(stream: Stream):
    active = [row for row in stream.offers if not row.removed]
    if not active:
        raise HTTPException(422, "A stream must have at least one active offer")
    pinned_total = sum(row.weight for row in active if row.pinned)
    if pinned_total > 100:
        raise HTTPException(422, "Pinned weights cannot exceed 100%")
    free = sorted(
        (row for row in active if not row.pinned), key=lambda row: (row.position, row.offer_id)
    )
    if not free and pinned_total != 100:
        raise HTTPException(422, "When every offer is pinned, their weights must total 100%")
    if free:
        quotient, remainder = divmod(100 - pinned_total, len(free))
        for index, row in enumerate(free):
            row.weight = quotient + (index < remainder)
    for row in stream.offers:
        if row.removed:
            row.weight = 0
            row.pinned = False


def stream_view(stream: Stream) -> StreamView:
    rows = (
        sorted(stream.offers, key=lambda row: (row.position, row.offer_id))
        if stream.editable
        else []
    )
    synced_at = stream.synced_at
    if synced_at and synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=timezone.utc)
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
        total_weight=sum(row.weight for row in rows if not row.removed),
        synced_at=synced_at.isoformat() if synced_at else None,
    )


def campaign_view(campaign: Campaign) -> CampaignView:
    return CampaignView(
        id=campaign.id,
        name=campaign.name,
        tracking_url=campaign.tracking_url,
        streams=[stream_view(stream) for stream in campaign.streams],
    )


def require_campaign(session: Session, campaign_id: int) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Fetch this campaign from Keitaro first")
    return campaign


def require_stream(session: Session, stream_id: int, version: int, settings: Settings) -> Stream:
    stream = session.get(Stream, stream_id)
    if not stream:
        raise HTTPException(404, "Stream has not been imported")
    if not stream.editable or not stream.campaign.name.startswith(settings.campaign_prefix):
        raise HTTPException(422, "Only offer streams in your test campaigns can be edited")
    if stream.version != version:
        raise HTTPException(409, "This stream changed in another tab; reload the campaign")
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


def sync_campaign(
    session: Session,
    client: KeitaroClient,
    settings: Settings,
    campaign_id: int,
    discard_draft: bool,
) -> Campaign:
    existing = session.get(Campaign, campaign_id)
    if existing and not discard_draft:
        if any(local_state(stream) != stream.published_state for stream in existing.streams):
            raise HTTPException(
                409, "This campaign has a local draft; cancel it or confirm discard"
            )
    remote_campaign = client.get(f"campaigns/{campaign_id}")
    remote_streams = client.get(f"campaigns/{campaign_id}/streams")
    update_offer_catalog(session, client.get("offers"))
    campaign = existing or Campaign(id=campaign_id, name=remote_campaign["name"])
    if not existing:
        session.add(campaign)
    campaign.name = remote_campaign["name"]
    domain = remote_campaign.get("domain")
    if isinstance(domain, dict):
        domain = domain.get("name")
    alias = remote_campaign.get("alias")
    campaign.tracking_url = None
    if domain and alias:
        if not domain.startswith(("https://", "http://")):
            domain = "https://" + domain
        campaign.tracking_url = domain.rstrip("/") + "/" + alias
    session.flush()
    by_id = {stream.id: stream for stream in campaign.streams}
    seen = set()
    for remote in remote_streams:
        stream_id = int(remote["id"])
        seen.add(stream_id)
        stream = by_id.get(stream_id)
        if not stream:
            stream = Stream(id=stream_id, campaign=campaign, name=remote["name"], position=0)
            session.add(stream)
            stream.version = 0
            stream.offers = []
        stream.name = remote["name"]
        stream.position = int(remote.get("position", 0))
        stream.editable = (
            remote.get("schema") == "landings"
            and remote.get("state") != "deleted"
            and campaign.name.startswith(settings.campaign_prefix)
        )
        stream.version += 1
        stream.synced_at = datetime.now(timezone.utc)
        stream.remote_offers = canonical_remote(remote.get("offers", []))
        old_rows = {row.offer_id: row for row in stream.offers}
        remote_ids = set()
        for index, item in enumerate(stream.remote_offers):
            offer_id = item["offer_id"]
            remote_ids.add(offer_id)
            offer = session.get(Offer, offer_id)
            if not offer:
                remote_offer = client.get(f"offers/{offer_id}")
                offer = Offer(id=offer_id, name=remote_offer["name"])
                session.add(offer)
                session.flush()
            row = old_rows.get(offer_id)
            if not row:
                row = StreamOffer(stream=stream, offer=offer, offer_id=offer_id)
                session.add(row)
                row.pinned = False
            elif row.removed or row.weight != item["share"]:
                row.pinned = False
            row.position = index
            row.removed = item["state"] != "active"
            row.weight = 0 if row.removed else item["share"]
        next_position = len(remote_ids)
        for row in stream.offers:
            if row.offer_id not in remote_ids:
                row.removed = True
                row.pinned = False
                row.weight = 0
                row.position = next_position
                next_position += 1
        session.flush()
        stream.published_state = local_state(stream)
    for stream in campaign.streams:
        if stream.id not in seen:
            stream.editable = False
            stream.version += 1
    session.commit()
    session.expire(campaign, ["streams"])
    return campaign


def add_offer(session: Session, client: KeitaroClient, stream: Stream, offer_id: int):
    existing = next((row for row in stream.offers if row.offer_id == offer_id), None)
    if existing and not existing.removed:
        raise HTTPException(422, "This offer is already active in the stream")
    remote = client.get(f"offers/{offer_id}")
    if remote.get("state") == "deleted":
        raise HTTPException(422, "An archived Keitaro offer cannot be added")
    update_offer_catalog(session, [remote])
    if existing:
        existing.removed = False
        existing.pinned = False
    else:
        row = StreamOffer(
            stream=stream,
            offer=session.get(Offer, offer_id),
            offer_id=offer_id,
            position=max((item.position for item in stream.offers), default=-1) + 1,
            weight=0,
            pinned=False,
            removed=False,
        )
        session.add(row)
    allocate(stream)
    stream.version += 1
    session.commit()


def patch_offer(session: Session, stream: Stream, offer_id: int, changes: PatchOffer):
    row = next((item for item in stream.offers if item.offer_id == offer_id), None)
    if not row:
        raise HTTPException(404, "Offer is not part of this stream")
    if changes.removed is not None:
        was_removed = row.removed
        row.removed = changes.removed
        if changes.removed or was_removed:
            row.pinned = False
    if row.removed and (changes.pinned is True or changes.weight is not None):
        raise HTTPException(422, "Restore the offer before assigning a weight")
    if changes.pinned is not None:
        row.pinned = changes.pinned
    if changes.weight is not None:
        row.weight = changes.weight
        row.pinned = True
    allocate(stream)
    stream.version += 1
    session.commit()


def cancel_stream(session: Session, stream: Stream):
    baseline = {item["offer_id"]: item for item in stream.published_state}
    for row in list(stream.offers):
        item = baseline.get(row.offer_id)
        if not item:
            stream.offers.remove(row)
            session.delete(row)
        else:
            for key in ("weight", "pinned", "removed", "position"):
                setattr(row, key, item[key])
    stream.version += 1
    session.commit()


def publish_stream(session: Session, client: KeitaroClient, stream: Stream):
    if local_state(stream) == stream.published_state:
        return
    allocate(stream)
    remote = client.get(f"streams/{stream.id}")
    if (
        int(remote.get("campaign_id", -1)) != stream.campaign_id
        or remote.get("schema") != "landings"
        or remote.get("state") == "deleted"
        or canonical_remote(remote.get("offers", [])) != stream.remote_offers
    ):
        raise HTTPException(409, "The stream changed in Keitaro; fetch it again before publishing")
    payload = [
        {"offer_id": row.offer_id, "share": row.weight, "state": "active"}
        for row in sorted(stream.offers, key=lambda row: (row.position, row.offer_id))
        if not row.removed
    ]
    active_ids = {row["offer_id"] for row in payload}
    payload.extend(
        row
        for row in stream.remote_offers
        if row["state"] == "disabled" and row["offer_id"] not in active_ids
    )
    published = client.put(f"streams/{stream.id}", {"offers": payload})
    actual = canonical_remote(published.get("offers", []))
    if actual != payload:
        raise UpstreamError(
            502, "Keitaro returned unexpected offer weights; fetch the campaign to reconcile", True
        )
    stream.remote_offers = actual
    stream.published_state = local_state(stream)
    stream.synced_at = datetime.now(timezone.utc)
    stream.version += 1
    session.commit()


def create_campaign(
    session: Session, client: KeitaroClient, settings: Settings, data: CreateCampaign
) -> Campaign:
    if not data.name.startswith(settings.campaign_prefix):
        raise HTTPException(422, f"Campaign name must start with {settings.campaign_prefix}")
    for path, value, label in (
        ("domains", data.domain_id, "domain"),
        ("groups?type=campaigns", data.group_id, "group"),
        ("traffic_sources", data.source_id, "source"),
    ):
        if value not in {int(item["id"]) for item in client.get(path)}:
            raise HTTPException(422, f"Choose an available Keitaro {label}")
    offer = client.get(f"offers/{data.offer_id}")
    if offer.get("state") == "deleted":
        raise HTTPException(422, "Choose an active Keitaro offer")
    remote_campaign = client.post(
        "campaigns",
        {
            "name": data.name,
            "alias": uuid4().hex,
            "type": "position",
            "state": "active",
            "domain_id": data.domain_id,
            "group_id": data.group_id,
            "traffic_source_id": data.source_id,
        },
    )
    campaign_id = int(remote_campaign["id"])
    common = {"campaign_id": campaign_id, "state": "active", "action_type": "http"}
    try:
        client.post(
            "streams",
            {
                **common,
                "name": f"Country {data.country_code} → Google",
                "type": "regular",
                "schema": "redirect",
                "position": 1,
                "action_payload": "https://google.com",
                "collect_clicks": True,
                "filters": [{"name": "country", "mode": "accept", "payload": [data.country_code]}],
            },
        )
        client.post(
            "streams",
            {
                **common,
                "name": "Offers",
                "type": "default",
                "schema": "landings",
                "position": 2,
                "collect_clicks": True,
                "landings": [],
                "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
            },
        )
    except UpstreamError as error:
        if error.ambiguous:
            raise UpstreamError(
                error.status,
                f"Campaign {campaign_id} exists; a stream write has an uncertain result. "
                "Inspect it in Keitaro before retrying",
                True,
            ) from error
        try:
            client.delete(f"campaigns/{campaign_id}")
        except UpstreamError:
            raise UpstreamError(
                502,
                f"Campaign {campaign_id} could not be completed or archived; inspect it in Keitaro",
            ) from error
        raise UpstreamError(
            error.status,
            f"Campaign setup failed; newly created campaign {campaign_id} was archived",
        ) from error
    try:
        return sync_campaign(session, client, settings, campaign_id, True)
    except UpstreamError as error:
        raise UpstreamError(
            error.status,
            f"Campaign {campaign_id} was created, but local import failed; fetch it by ID",
        ) from error
