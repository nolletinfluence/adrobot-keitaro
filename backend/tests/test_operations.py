from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, Lock, get_ident

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Operation


def create_payload(**changes):
    return {
        "name": "test_borkoev_operation",
        "country_code": "KG",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 2,
        "source_id": 3,
        **changes,
    }


def draft(client):
    fetched = client.post("/api/campaigns/101/fetch", json={}).json()["streams"][1]
    added = client.post(
        "/api/streams/202/offers", json={"offer_id": 12, "version": fetched["version"]}
    ).json()
    response = client.patch(
        "/api/streams/202/offers/11", json={"weight": 25, "version": added["version"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def publish(client, stream, key="publish-attempt"):
    return client.post(
        "/api/streams/202/publish",
        json={"version": stream["version"]},
        headers={"Idempotency-Key": key},
    )


def operation(client, response):
    operation_id = response.headers.get("X-Operation-ID") or response.json()["operation_id"]
    result = client.get(f"/api/operations/{operation_id}")
    assert result.status_code == 200
    return result.json()


def test_creator_replay_returns_original_result_and_rejects_different_payload(system):
    client, remote, _application = system
    headers = {"Idempotency-Key": "create-attempt"}
    first = client.post("/api/campaigns", json=create_payload(), headers=headers)
    assert first.status_code == 201, first.text
    calls = deepcopy(remote.calls)
    replay = client.post("/api/campaigns", json=create_payload(), headers=headers)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotent-Replay"] == "true"
    assert remote.calls == calls
    conflict = client.post("/api/campaigns", json=create_payload(offer_id=12), headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert remote.calls == calls


def test_campaign_post_applied_then_timeout_preserves_alias_and_never_repeats_post(system):
    client, remote, application = system
    remote.after_failure = ("POST", "campaigns", "timeout")
    headers = {"Idempotency-Key": "lost-create-response"}
    response = client.post("/api/campaigns", json=create_payload(), headers=headers)
    assert response.status_code == 504
    saved = operation(client, response)
    assert saved["status"] == "uncertain"
    with application.state.sessions() as session:
        journal = session.get(Operation, saved["id"])
        assert journal.context_json["alias"] == remote.campaigns[102]["alias"]
    retry = client.post("/api/campaigns", json=create_payload(), headers=headers)
    assert retry.status_code == 409
    assert retry.json()["operation_id"] == saved["id"]
    posts = sum(method == "POST" for method, _path, _body in remote.calls)
    reconciled = client.post(f"/api/operations/{saved['id']}/reconcile")
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "uncertain"
    assert reconciled.json()["campaign_id"] == 102
    assert reconciled.json()["error_code"] == "CREATION_INCOMPLETE"
    assert len(remote.campaigns) == 2
    assert sum(method == "POST" for method, _path, _body in remote.calls) == posts


def test_complete_creation_is_recovered_after_restart_without_remote_writes(system):
    client, remote, application = system
    count = [0]

    def lose_second_stream_response(method, path, _response):
        if (method, path) == ("POST", "streams"):
            count[0] += 1
            if count[0] == 2:
                remote.after_failure = ("POST", "streams", "timeout")

    remote.after_request = lose_second_stream_response
    response = client.post(
        "/api/campaigns", json=create_payload(), headers={"Idempotency-Key": "restart-create"}
    )
    assert response.status_code == 504
    saved = operation(client, response)
    remote.after_request = None
    calls = len(remote.calls)
    restarted = create_app(application.state.service.settings, httpx.MockTransport(remote.handle))
    with TestClient(restarted) as recovered:
        result = recovered.post(f"/api/operations/{saved['id']}/reconcile")
        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        assert recovered.get("/api/campaigns/102").status_code == 200
        replay = recovered.post(
            "/api/campaigns", json=create_payload(), headers={"Idempotency-Key": "restart-create"}
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == 102
    assert all(method == "GET" for method, _path, _body in remote.calls[calls:])


def test_applied_publication_timeout_freezes_draft_until_read_only_reconciliation(system):
    client, remote, _application = system
    stream = draft(client)
    remote.after_failure = ("PUT", "streams/202", "timeout")
    response = publish(client, stream)
    assert response.status_code == 504
    assert response.json()["outcome"] == "unknown"
    saved = operation(client, response)
    current = client.get("/api/campaigns/101").json()["streams"][1]
    assert current["operation"]["id"] == saved["id"]
    assert current["dirty"] is True
    for path, body, method in [
        ("/api/streams/202/cancel", {"version": current["version"]}, "POST"),
        ("/api/streams/202/offers", {"version": current["version"], "offer_id": 13}, "POST"),
        ("/api/streams/202/offers/11", {"version": current["version"], "pinned": False}, "PATCH"),
        ("/api/campaigns/101/fetch", {"discard_draft": True}, "POST"),
    ]:
        blocked = client.request(method, path, json=body)
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "OPERATION_PENDING"
    calls = len(remote.calls)
    reconciled = client.post(f"/api/operations/{saved['id']}/reconcile")
    assert reconciled.json()["status"] == "succeeded"
    assert all(method == "GET" for method, _path, _body in remote.calls[calls:])
    current = client.get("/api/campaigns/101").json()["streams"][1]
    assert current["operation"] is None and current["dirty"] is False
    assert current["version"] == stream["version"] + 1
    assert [(row["weight"], row["pinned"]) for row in current["offers"]] == [
        (25, True),
        (75, False),
    ]
    assert current["published_offers"] == current["offers"]


def test_publication_replay_does_not_repeat_put_or_increment_version(system):
    client, remote, _application = system
    stream = draft(client)
    first = publish(client, stream)
    assert first.status_code == 200, first.text
    calls = deepcopy(remote.calls)
    replay = publish(client, stream)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["Idempotent-Replay"] == "true"
    assert remote.calls == calls


def test_commit_failure_after_remote_publication_is_reconciled_with_saved_pins(system):
    client, remote, _application = system
    stream = draft(client)
    fail_once = [True]

    def before_commit(session):
        if fail_once[0] and any(
            isinstance(row, Operation)
            and row.kind == "publish_stream"
            and row.status == "succeeded"
            for row in session.identity_map.values()
        ):
            fail_once[0] = False
            raise OperationalError("COMMIT", {}, RuntimeError("simulated disk failure"))

    event.listen(Session, "before_commit", before_commit)
    try:
        response = publish(client, stream)
    finally:
        event.remove(Session, "before_commit", before_commit)
    assert response.status_code == 500
    assert response.json()["code"] == "LOCAL_PERSISTENCE_ERROR"
    saved = operation(client, response)
    assert saved["status"] == "uncertain"
    puts = sum(method == "PUT" for method, _path, _body in remote.calls)
    assert client.post(f"/api/operations/{saved['id']}/reconcile").json()["status"] == "succeeded"
    assert sum(method == "PUT" for method, _path, _body in remote.calls) == puts
    current = client.get("/api/campaigns/101").json()["streams"][1]
    assert current["offers"][0]["pinned"] is True
    assert current["offers"][0]["weight"] == 25


def test_pending_journal_survives_lost_error_update_and_application_restart(system, monkeypatch):
    client, remote, application = system
    stream = draft(client)
    remote.after_failure = ("PUT", "streams/202", "timeout")

    def unavailable(*_args):
        raise OperationalError("COMMIT", {}, RuntimeError("simulated storage unavailable"))

    monkeypatch.setattr(application.state.service, "_record_error", unavailable)
    response = publish(client, stream)
    saved = operation(client, response)
    assert saved["status"] == "pending"
    restarted = create_app(application.state.service.settings, httpx.MockTransport(remote.handle))
    calls = len(remote.calls)
    with TestClient(restarted) as recovered:
        result = recovered.post(f"/api/operations/{saved['id']}/reconcile")
        assert result.json()["status"] == "succeeded"
        assert recovered.get("/api/campaigns/101").json()["streams"][1]["dirty"] is False
    assert all(method == "GET" for method, _path, _body in remote.calls[calls:])


def test_reconciliation_reports_remote_conflict_without_overwriting_it(system):
    client, remote, _application = system
    stream = draft(client)
    remote.after_failure = ("PUT", "streams/202", "timeout")
    saved = operation(client, publish(client, stream))
    remote.streams[202]["offers"] = [{"offer_id": 13, "share": 100, "state": "active"}]
    before = deepcopy(remote.streams[202])
    calls = len(remote.calls)
    response = client.post(f"/api/operations/{saved['id']}/reconcile")
    assert response.json()["status"] == "conflict"
    assert response.json()["error_code"] == "REMOTE_CONFLICT"
    assert remote.streams[202] == before
    assert all(method == "GET" for method, _path, _body in remote.calls[calls:])
    current = client.get("/api/campaigns/101").json()["streams"][1]
    assert current["dirty"] is True and current["operation"] is None


def test_concurrent_replay_publishes_once_and_local_reads_remain_available(system):
    client, remote, _application = system
    stream = draft(client)
    entered, release = Event(), Event()

    def block_write(method, path, _payload):
        if (method, path) == ("PUT", "streams/202"):
            entered.set()
            assert release.wait(timeout=5)

    remote.before_request = block_write
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(publish, client, stream)
        assert entered.wait(timeout=5)
        second = executor.submit(publish, client, stream)
        read = executor.submit(client.get, "/api/campaigns/101")
        try:
            snapshot = read.result(timeout=2)
            assert snapshot.status_code == 200
            assert snapshot.json()["streams"][1]["operation"]["status"] == "pending"
        finally:
            release.set()
        one, two = first.result(timeout=5), second.result(timeout=5)
    assert one.status_code == two.status_code == 200
    assert one.json() == two.json()
    assert sum(method == "PUT" for method, _path, _body in remote.calls) == 1


def test_errors_have_safe_machine_codes_and_correlated_request_ids(system):
    client, _remote, _application = system
    response = client.post(
        "/api/campaigns",
        json=create_payload(country_code="ZZ"),
        headers={"X-Request-ID": "review-case-1"},
    )
    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "review-case-1"
    assert response.json()["request_id"] == "review-case-1"
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["outcome"] == "not_applied"
    assert "test-key" not in response.text
    assert client.get("/api/operations?limit=101").status_code == 422


def test_reordered_provider_allocations_reconcile_without_changing_local_rounding_order(system):
    client, remote, _application = system
    remote.streams[202]["offers"] = [
        {"offer_id": 12, "share": 50, "state": "active"},
        {"offer_id": 11, "share": 50, "state": "active"},
    ]
    stream = client.post("/api/campaigns/101/fetch", json={}).json()["streams"][1]
    stream = client.post(
        "/api/streams/202/offers", json={"version": stream["version"], "offer_id": 13}
    ).json()
    assert [(row["id"], row["weight"]) for row in stream["offers"]] == [
        (12, 34),
        (11, 33),
        (13, 33),
    ]
    remote.reverse_offer_responses = True
    remote.after_failure = ("PUT", "streams/202", "timeout")
    response = publish(client, stream, "reordered-publication")
    assert response.status_code == 504
    saved = operation(client, response)
    calls = len(remote.calls)
    result = client.post(f"/api/operations/{saved['id']}/reconcile")
    assert result.json()["status"] == "succeeded"
    assert all(method == "GET" for method, _path, _body in remote.calls[calls:])
    stream = client.post("/api/campaigns/101/fetch", json={}).json()["streams"][1]
    assert [(row["id"], row["weight"]) for row in stream["offers"]] == [
        (12, 34),
        (11, 33),
        (13, 33),
    ]


def test_database_binding_blocks_cached_replay_and_reconciliation_against_another_target(system):
    client, remote, application = system
    headers = {"Idempotency-Key": "target-bound-create"}
    response = client.post("/api/campaigns", json=create_payload(), headers=headers)
    saved = operation(client, response)
    settings = application.state.service.settings.model_copy(
        update={"keitaro_base_url": "https://another.example/admin_api/v1"}
    )
    other = create_app(settings, httpx.MockTransport(remote.handle))
    calls = len(remote.calls)
    with TestClient(other) as changed:
        replay = changed.post("/api/campaigns", json=create_payload(), headers=headers)
        assert replay.status_code == 409
        assert replay.json()["code"] == "DATABASE_TARGET_MISMATCH"
        assert changed.post(f"/api/operations/{saved['id']}/reconcile").status_code == 409
        assert changed.get("/api/campaigns/101").status_code == 409
    assert len(remote.calls) == calls


def test_demo_settings_isolate_database_without_binding_to_live_url_or_key(tmp_path):
    original = f"sqlite:///{tmp_path / 'adrobot.db'}"
    one = Settings(
        database_url=original,
        keitaro_mode="demo",
        keitaro_base_url="https://one.example",
        keitaro_api_key="first-real-key",
    )
    two = Settings(
        database_url=original,
        keitaro_mode="demo",
        keitaro_base_url="https://two.example",
        keitaro_api_key="second-real-key",
    )
    live = Settings(database_url=original, keitaro_mode="live")
    assert live.database_url == original
    assert one.database_url == two.database_url != original
    assert one.database_url.endswith("adrobot-demo.db")
    assert (
        Settings(database_url=one.database_url, keitaro_mode="demo").database_url
        == one.database_url
    )
    app_one = create_app(one, httpx.MockTransport(lambda _request: httpx.Response(200, json=[])))
    app_two = create_app(two, httpx.MockTransport(lambda _request: httpx.Response(200, json=[])))
    with TestClient(app_one) as first, TestClient(app_two) as second:
        assert app_one.state.service.target == app_two.state.service.target
        assert first.get("/api/health").json()["keitaro_configured"] is True
        assert second.get("/api/health").json()["mode"] == "demo"


def test_foreign_operation_context_cannot_be_replayed_or_reconciled(system):
    client, remote, application = system
    headers = {"Idempotency-Key": "foreign-journal"}
    response = client.post("/api/campaigns", json=create_payload(), headers=headers)
    saved = operation(client, response)
    with application.state.sessions.begin() as session:
        row = session.get(Operation, saved["id"])
        row.context_json = {
            **row.context_json,
            "target": {"mode": "live", "base_url": "https://foreign.example/admin_api/v1"},
        }
    calls = len(remote.calls)
    replay = client.post("/api/campaigns", json=create_payload(), headers=headers)
    assert replay.status_code == 409
    assert replay.json()["code"] == "OPERATION_TARGET_MISMATCH"
    reconciled = client.post(f"/api/operations/{saved['id']}/reconcile")
    assert reconciled.status_code == 409
    assert reconciled.json()["code"] == "OPERATION_TARGET_MISMATCH"
    assert len(remote.calls) == calls


@pytest.mark.parametrize("completion_path", ["create", "reconcile"])
def test_creation_bundle_and_completion_cannot_overwrite_a_concurrent_publication(
    system, monkeypatch, completion_path
):
    client, remote, application = system
    saved = None
    if completion_path == "reconcile":
        stream_posts = [0]

        def lose_last_response(method, path, _response):
            if (method, path) == ("POST", "streams"):
                stream_posts[0] += 1
                if stream_posts[0] == 2:
                    remote.after_failure = ("POST", "streams", "timeout")

        remote.after_request = lose_last_response
        response = client.post(
            "/api/campaigns", json=create_payload(), headers={"Idempotency-Key": "creation-lock"}
        )
        assert response.status_code == 504
        saved = operation(client, response)

    captured, release, editor_attempted = Event(), Event(), Event()
    captured_thread = [None]
    paused = [False]
    real_lock = application.state.service.campaign_locks.get(102)

    class ObservedLock:
        def __init__(self):
            self.guard = Lock()
            self.owner = None
            self.depth = 0

        def __enter__(self):
            if captured.is_set() and not release.is_set():
                editor_attempted.set()
            real_lock.acquire()
            with self.guard:
                self.owner = get_ident()
                self.depth += 1
            return self

        def __exit__(self, *_args):
            with self.guard:
                self.depth -= 1
                if not self.depth:
                    self.owner = None
            real_lock.release()

    observed = ObservedLock()
    original_get = application.state.service.campaign_locks.get
    monkeypatch.setattr(
        application.state.service.campaign_locks,
        "get",
        lambda key: observed if key == 102 else original_get(key),
    )

    def pause_captured_bundle(method, path, _response):
        if (method, path) == ("GET", "campaigns/102/streams") and not paused[0]:
            paused[0] = True
            captured_thread[0] = get_ident()
            captured.set()
            assert release.wait(timeout=5)

    remote.after_request = pause_captured_bundle

    def edit_visible_campaign():
        imported = client.post("/api/campaigns/102/fetch", json={})
        assert imported.status_code == 200, imported.text
        stream = imported.json()["streams"][1]
        added = client.post(
            f"/api/streams/{stream['id']}/offers",
            json={"offer_id": 12, "version": stream["version"]},
        )
        assert added.status_code == 200, added.text
        return client.post(
            f"/api/streams/{stream['id']}/publish",
            json={"version": added.json()["version"]},
            headers={"Idempotency-Key": "concurrent-editor"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        if completion_path == "create":
            creation = executor.submit(
                client.post,
                "/api/campaigns",
                json=create_payload(),
                headers={"Idempotency-Key": "creation-lock"},
            )
        else:
            creation = executor.submit(client.post, f"/api/operations/{saved['id']}/reconcile")
        assert captured.wait(timeout=5)
        editing = executor.submit(edit_visible_campaign)
        try:
            assert editor_attempted.wait(timeout=5)
            with observed.guard:
                assert observed.owner == captured_thread[0]
        finally:
            release.set()
        finished = creation.result(timeout=5)
        published = editing.result(timeout=5)
    assert finished.status_code == (201 if completion_path == "create" else 200), finished.text
    assert published.status_code == 200, published.text
    current = client.get("/api/campaigns/102").json()["streams"][1]
    assert {row["id"]: row["weight"] for row in current["offers"]} == {11: 50, 12: 50}
    assert current["dirty"] is False
    assert remote.streams[current["id"]]["offers"] == [
        {"offer_id": 11, "share": 50, "state": "active"},
        {"offer_id": 12, "share": 50, "state": "active"},
    ]
