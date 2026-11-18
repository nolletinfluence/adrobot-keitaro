from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

from app.models import Offer


def fetch(client, **kwargs):
    response = client.post("/api/campaigns/101/fetch", json=kwargs)
    assert response.status_code == 200, response.text
    return response.json()["streams"][1]


def add(client, stream, offer_id):
    response = client.post(
        "/api/streams/202/offers", json={"version": stream["version"], "offer_id": offer_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def patch(client, stream, offer_id, **changes):
    response = client.patch(
        f"/api/streams/202/offers/{offer_id}", json={"version": stream["version"], **changes}
    )
    assert response.status_code == 200, response.text
    return response.json()


def publish(client, stream):
    response = client.post("/api/streams/202/publish", json={"version": stream["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def weights(stream):
    return {row["id"]: row["weight"] for row in stream["offers"]}


def creation_payload(**changes):
    return {
        "name": "test_borkoev_created",
        "country_code": "kg",
        "offer_id": 11,
        "domain_id": 1,
        "group_id": 2,
        "source_id": 3,
        **changes,
    }


def test_draft_pin_and_cancel_preserve_remote_and_offer_catalog(system):
    client, remote, application = system
    stream = fetch(client)
    initial_remote = deepcopy(remote.streams)
    stream = add(client, stream, 12)
    stream = add(client, stream, 13)
    assert weights(stream) == {11: 34, 12: 33, 13: 33}
    stream = patch(client, stream, 11, weight=25)
    assert weights(stream) == {11: 25, 12: 38, 13: 37}
    assert stream["total_weight"] == 100
    assert stream["dirty"] is True
    assert remote.streams == initial_remote
    response = client.post("/api/streams/202/cancel", json={"version": stream["version"]})
    assert response.status_code == 200
    assert weights(response.json()) == {11: 100}
    assert response.json()["dirty"] is False
    with application.state.sessions() as session:
        assert session.get(Offer, 12) is not None
        assert session.get(Offer, 13) is not None


def test_publish_archive_cancel_and_restore_keep_published_rows(system):
    client, remote, _application = system
    stream = publish(client, add(client, fetch(client), 12))
    assert stream["dirty"] is False
    before = deepcopy(remote.streams[202])
    stream = patch(client, stream, 12, removed=True)
    assert weights(stream) == {11: 100, 12: 0}
    stream = publish(client, stream)
    assert remote.streams[202]["offers"] == [{"offer_id": 11, "share": 100, "state": "active"}]
    assert remote.streams[202]["filters"] == before["filters"]
    assert remote.streams[202]["landings"] == before["landings"]
    assert remote.streams[201]["action_payload"] == "https://google.com"
    assert stream["offers"][1]["removed"] is True
    stream = patch(client, stream, 12, removed=False)
    assert weights(stream) == {11: 50, 12: 50}
    response = client.post("/api/streams/202/cancel", json={"version": stream["version"]})
    assert response.status_code == 200
    assert response.json()["offers"][1]["removed"] is True
    stream = patch(client, response.json(), 12, removed=False)
    stream = publish(client, stream)
    assert [item["share"] for item in remote.streams[202]["offers"]] == [50, 50]


def test_fetch_rejects_dirty_draft_and_archives_remote_deleted_offer(system):
    client, remote, _application = system
    stream = add(client, fetch(client), 12)
    response = client.post("/api/campaigns/101/fetch", json={})
    assert response.status_code == 409
    stream = publish(client, stream)
    remote.streams[202]["offers"] = [{"offer_id": 11, "share": 100, "state": "active"}]
    stream = fetch(client)
    assert weights(stream) == {11: 100, 12: 0}
    assert stream["offers"][1]["removed"] is True
    assert stream["dirty"] is False


def test_explicit_discard_resyncs_and_local_stale_version_is_rejected(system):
    client, _remote, _application = system
    stream = fetch(client)
    stale_version = stream["version"]
    stream = add(client, stream, 12)
    response = client.patch(
        "/api/streams/202/offers/11", json={"version": stale_version, "weight": 25}
    )
    assert response.status_code == 409
    stream = fetch(client, discard_draft=True)
    assert stream["dirty"] is False
    assert weights(stream)[11] == 100


def test_upstream_drift_and_failure_preserve_draft_without_remote_write(system):
    client, remote, _application = system
    stream = add(client, fetch(client), 12)
    remote.streams[202]["offers"][0]["share"] = 90
    response = client.post("/api/streams/202/publish", json={"version": stream["version"]})
    assert response.status_code == 409
    assert not any(method == "PUT" for method, _path, _body in remote.calls)
    remote.streams[202]["offers"][0]["share"] = 100
    remote.failure = ("PUT", "streams/202", 400)
    response = client.post("/api/streams/202/publish", json={"version": stream["version"]})
    assert response.status_code == 502
    assert "test-key" not in response.text
    persisted = client.get("/api/campaigns/101").json()["streams"][1]
    assert persisted["dirty"] is True
    assert persisted["version"] == stream["version"]
    assert weights(persisted) == {11: 50, 12: 50}


def test_empty_active_and_invalid_pins_leave_persisted_state_unchanged(system):
    client, _remote, _application = system
    stream = fetch(client)
    for changes in ({"removed": True}, {"weight": 25}):
        response = client.patch(
            "/api/streams/202/offers/11", json={"version": stream["version"], **changes}
        )
        assert response.status_code == 422
    persisted = client.get("/api/campaigns/101").json()["streams"][1]
    assert weights(persisted) == {11: 100}
    assert persisted["version"] == stream["version"]
    stream = add(client, stream, 12)
    stream = patch(client, stream, 11, weight=75)
    response = client.patch(
        "/api/streams/202/offers/12", json={"version": stream["version"], "weight": 30}
    )
    assert response.status_code == 422
    assert weights(client.get("/api/campaigns/101").json()["streams"][1]) == {11: 75, 12: 25}


def test_non_offer_and_foreign_campaign_streams_cannot_be_edited(system):
    client, remote, _application = system
    fetched = client.post("/api/campaigns/101/fetch", json={}).json()
    assert fetched["streams"][0]["editable"] is False
    assert fetched["streams"][0]["offers"] == []
    response = client.post("/api/streams/201/offers", json={"version": 1, "offer_id": 12})
    assert response.status_code == 422
    remote.campaigns[101]["name"] = "somebody_else_campaign"
    foreign = client.post("/api/campaigns/101/fetch", json={}).json()
    assert all(not row["editable"] for row in foreign["streams"])
    response = client.post(
        "/api/streams/202/offers",
        json={"version": foreign["streams"][1]["version"], "offer_id": 12},
    )
    assert response.status_code == 422


def test_creator_uses_ordered_country_redirect_and_fallback_offer(system):
    client, remote, _application = system
    response = client.post("/api/campaigns", json=creation_payload())
    assert response.status_code == 201, response.text
    result = response.json()
    campaign = remote.campaigns[result["id"]]
    assert len(campaign["alias"]) == 32
    assert all(character in "0123456789abcdef" for character in campaign["alias"])
    assert result["tracking_url"] == f"https://tracker.example/{campaign['alias']}"
    assert campaign["type"] == "position"
    assert (campaign["domain_id"], campaign["group_id"], campaign["traffic_source_id"]) == (1, 2, 3)
    streams = [row for row in remote.streams.values() if row["campaign_id"] == result["id"]]
    first, second = sorted(streams, key=lambda row: row["position"])
    assert first["schema"] == "redirect"
    assert first["action_payload"] == "https://google.com"
    assert first["filters"] == [{"name": "country", "mode": "accept", "payload": ["KG"]}]
    assert second["type"] == "default"
    assert second["offers"] == [{"offer_id": 11, "share": 100, "state": "active"}]


def test_creator_rejects_non_test_prefix_and_unknown_catalog_ids(system):
    client, remote, _application = system
    for changes in (
        {"name": "real_campaign"},
        {"domain_id": 999},
        {"country_code": "invalid"},
        {"country_code": "ZZ"},
        {"country_code": "XX"},
    ):
        response = client.post("/api/campaigns", json=creation_payload(**changes))
        assert response.status_code == 422
    assert not any(method == "POST" for method, _path, _body in remote.calls)


def test_creator_archives_only_its_own_campaign_on_definite_failure(system):
    client, remote, _application = system
    remote.failure = ("POST", "streams", 400)
    response = client.post("/api/campaigns", json=creation_payload())
    assert response.status_code == 502
    assert "archived" in response.json()["detail"]
    assert remote.campaigns[102]["state"] == "deleted"
    assert remote.campaigns[101]["state"] == "active"
    assert [path for method, path, _body in remote.calls if method == "DELETE"] == ["campaigns/102"]


def test_creator_does_not_rollback_or_retry_ambiguous_stream_write(system):
    client, remote, _application = system
    remote.failure = ("POST", "streams", "timeout")
    response = client.post("/api/campaigns", json=creation_payload())
    assert response.status_code == 504
    assert "102" in response.json()["detail"]
    assert "uncertain" in response.json()["detail"]
    assert remote.campaigns[102]["state"] == "active"
    assert not any(method == "DELETE" for method, _path, _body in remote.calls)
    assert sum(method == "POST" and path == "streams" for method, path, _body in remote.calls) == 1


def test_offer_search_is_filtered_and_bounded(system):
    client, _remote, _application = system
    response = client.get("/api/offers?q=Offer&limit=2")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert response.json()["has_more"] is True
    assert client.get("/api/offers?q=12").json()["items"] == [{"id": 12, "name": "Offer 12"}]
    assert client.get("/api/offers?limit=101").status_code == 422


def test_slow_remote_catalog_does_not_block_unrelated_database_work(system):
    client, remote, _application = system
    stream = fetch(client)
    catalog_entered = Event()
    release_catalog = Event()
    offer_add_entered = Event()

    def pause_catalog(method, path, _payload):
        if (method, path) == ("GET", "offers"):
            catalog_entered.set()
            assert release_catalog.wait(timeout=5)
        elif (method, path) == ("GET", "offers/12"):
            offer_add_entered.set()

    remote.before_request = pause_catalog
    with ThreadPoolExecutor(max_workers=2) as executor:
        catalog_result = executor.submit(client.get, "/api/offers")
        assert catalog_entered.wait(timeout=5)
        add_result = executor.submit(
            client.post,
            "/api/streams/202/offers",
            json={"version": stream["version"], "offer_id": 12},
        )
        try:
            assert offer_add_entered.wait(timeout=2)
            response = add_result.result(timeout=2)
            assert response.status_code == 200, response.text
            assert client.get("/api/campaigns/101").status_code == 200
        finally:
            release_catalog.set()
        assert catalog_result.result(timeout=5).status_code == 200
    assert weights(response.json()) == {11: 50, 12: 50}
