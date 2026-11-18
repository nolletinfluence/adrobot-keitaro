# API contract

All endpoints are relative to `/api`. IDs are Keitaro integer IDs. Responses and payloads use snake_case. The backend owns all weight calculations; the frontend never sends computed allocations. Keitaro secrets never appear in responses.

- `GET /health` -> `{status, keitaro_configured}`.
- `GET /catalogs` -> `{domains: [{id,name}], groups: [{id,name}], sources: [{id,name}], campaign_prefix: string}`.
- `GET /offers?q=<id-or-name>&limit=20` -> `{items: [{id,name}], has_more: boolean}`.
- `GET /campaigns?q=<name-or-id>` -> `{items: [{id,name}]}`; only the configured test campaign prefix is writable.
- `POST /campaigns` body `{name, country_code, offer_id, domain_id, group_id, source_id}` -> CampaignDetail, status 201. Name must start with the configured prefix.
- `POST /campaigns/{id}/fetch` body `{discard_draft: false}` -> CampaignDetail. Import or re-sync remote data. Refuse dirty local drafts by default.
- `GET /campaigns/{id}` -> locally persisted CampaignDetail, or 404 before import.
- `POST /streams/{id}/offers` body `{offer_id, version}` -> Stream.
- `PATCH /streams/{id}/offers/{offer_id}` body `{removed?: boolean, pinned?: boolean, weight?: integer, version}` -> Stream. Removing clears its pinned state; restoring returns it unpinned. Weight is explicitly pinned when manually changed.
- `POST /streams/{id}/cancel` body `{version}` -> Stream.
- `POST /streams/{id}/publish` body `{version}` -> Stream. Reject upstream drift; retain draft on API failure. Never blindly retry an ambiguous upstream write.

CampaignDetail: `{id, name, tracking_url: string|null, streams: Stream[]}`.

Stream: `{id, campaign_id, name, editable: boolean, dirty: boolean, version: integer, offers: StreamOffer[], total_weight: integer, synced_at: string|null}`.

StreamOffer: `{id, name, weight: integer, pinned: boolean, removed: boolean}`. Archived offers have weight zero. Non-offer streams have editable=false and offers=[] and are never rewritten by editor operations.

Errors use FastAPI `detail` (string or validation list). Use 422 for invalid allocations/inputs, 409 for stale local version, draft conflicts or remote drift; 502/504 for upstream failures with redacted safe messages. Request timeout on the frontend must allow backend upstream timeout plus a margin.

Pinned active shares are exact integers and cannot exceed 100. Unpinned active offers divide remaining weight using deterministic largest remainder by stable offer order, so the total is exactly 100. Reject empty active sets, and reject an all-pinned state whose sum is not 100. Inactive archived rows are not part of allocations.
