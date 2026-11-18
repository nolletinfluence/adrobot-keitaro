# API contract

All paths are relative to `/api`. IDs are Keitaro integer IDs except operation IDs. JSON uses snake_case. The server is authoritative for weights, versions and outcomes. OpenAPI is available at `/docs`.

## Read and draft operations

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/health` | — | `{status, keitaro_configured, mode}` |
| GET | `/catalogs` | — | `{domains, groups, sources, campaign_prefix, mode}`; catalog entries `{id,name}` |
| GET | `/offers` | `q`, `limit=20` | `{items:[{id,name}],has_more}` |
| GET | `/campaigns` | `q` | `{items:[{id,name}]}`, filtered by configured test prefix |
| GET | `/campaigns/{id}` | — | persisted CampaignDetail |
| POST | `/campaigns/{id}/fetch` | `{discard_draft:false}` | CampaignDetail; dirty drafts and pending publications are protected |
| POST | `/streams/{id}/offers` | `{offer_id,version}` | Stream |
| PATCH | `/streams/{id}/offers/{offer_id}` | `{version,removed?,pinned?,weight?}` | Stream |
| POST | `/streams/{id}/cancel` | `{version}` | Stream; rejected while publication outcome is unknown |

## Idempotent remote writes

### Create a campaign

`POST /campaigns` with optional `Idempotency-Key`:

```json
{
  "name": "test_borkoev_example",
  "country_code": "KG",
  "offer_id": 11,
  "domain_id": 1,
  "group_id": 2,
  "source_id": 3
}
```

Returns 201 CampaignDetail. The operation persists its stable alias before POSTing to Keitaro. A repeated key with identical input returns the stored result and `Idempotent-Replay: true`; another payload returns 409. Without a key, a new key is generated for compatibility. The supplied frontend always uses and persists its own key before submitting.

### Publish a stream

`POST /streams/{id}/publish`, body `{version}`, optional `Idempotency-Key`. Returns Stream. The saved operation includes the immutable local draft, baseline and intended remote offers. A reserved stream cannot be edited, cancelled or synced until the outcome is settled.

Responses identify the journal entry with `X-Operation-ID`. Successful replay does not send another remote write or increment the version again. Stored replay responses describe the original operation; GET the campaign for its latest state.

## Operation journal

- `GET /operations?limit=20` → `{items:Operation[]}`.
- `GET /operations/{id}` → Operation.
- `POST /operations/{id}/reconcile`, body `{}` → Operation.

Reconciliation performs only remote **GET** requests. It never POSTs, PUTs or DELETEs in Keitaro. A matching remote publication finalizes the saved pin snapshot. A complete campaign matching its stable alias and both expected streams can be imported. A missing/partial result stays uncertain; the API does not infer failure from absence alone.

```typescript
type Operation = {
  id: string;
  kind: 'create_campaign' | 'publish_stream';
  status: 'pending' | 'uncertain' | 'succeeded' | 'failed' | 'conflict';
  phase: string;
  campaign_id: number | null;
  stream_id: number | null;
  detail: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};
```

## Snapshots

```typescript
type StreamOffer = {
  id: number; name: string; weight: number; pinned: boolean; removed: boolean;
};
type Stream = {
  id: number; campaign_id: number; name: string;
  editable: boolean; dirty: boolean; version: number;
  offers: StreamOffer[]; published_offers: StreamOffer[];
  operation: Operation | null;
  total_weight: number; synced_at: string | null;
};
type CampaignDetail = {
  id: number; name: string; tracking_url: string | null; streams: Stream[];
};
```

`operation` contains a pending/uncertain publication only. Non-offer streams remain opaque and expose no edit controls. Archived offers have zero weight. Integer allocation uses pinned shares plus deterministic remainder distribution; total active weight is 100. Manual weight changes pin the offer. Removing/restoring clears its pin.

## Errors and correlation

```json
{
  "detail": "Safe description without provider credentials",
  "code": "OPERATION_RECONCILIATION_REQUIRED",
  "operation_id": "operation-id",
  "outcome": "unknown",
  "request_id": "request-id"
}
```

`detail` remains compatible with FastAPI validation lists. `outcome` is `not_applied`, `unknown`, `applied` or null. Every response carries `X-Request-ID`. The server validates incoming correlation IDs before reusing them.

- 422: invalid input/allocation or unavailable references.
- 409: stale version, a protected draft, unfinished operation, target mismatch, changed idempotency payload or remote conflict.
- 502/504: provider/connection/deadline failures. An unknown write outcome must be reconciled before another mutation.
- No raw provider response, API key or authorization header is returned or logged.

## Demo controls

`POST /demo/fault`, body `{kind:"publish_timeout"}` or `{kind:"create_timeout"}`. Returns `{armed:kind}`. Only available with the local demo provider; live mode returns 404.

The next matching write is persisted and then raises a simulated read timeout. `publish_timeout` demonstrates the complete recovery path. `create_timeout` intentionally produces a partial campaign requiring inspection; it does not auto-create missing streams.
