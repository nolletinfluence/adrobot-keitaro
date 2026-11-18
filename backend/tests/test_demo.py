from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo import DemoKeitaro
from app.main import create_app


def test_demo_preserves_published_allocations_across_restart(tmp_path):
    path = tmp_path / "demo.json"
    demo = DemoKeitaro(path, "test_borkoev_")
    with httpx.Client(
        base_url="https://demo.invalid/admin_api/v1/", transport=httpx.MockTransport(demo)
    ) as client:
        response = client.put(
            "streams/202",
            json={
                "offers": [
                    {"offer_id": 11, "share": 25, "state": "active"},
                    {"offer_id": 14, "share": 75, "state": "active"},
                ]
            },
        )
        assert response.status_code == 200
    restarted = DemoKeitaro(path, "test_borkoev_")
    with httpx.Client(
        base_url="https://demo.invalid/admin_api/v1/", transport=httpx.MockTransport(restarted)
    ) as client:
        stream = client.get("streams/202").json()
    assert [item["share"] for item in stream["offers"]] == [25, 75]
    assert stream["type"] == "default"


def test_demo_rejects_invalid_write_without_persisting_it(tmp_path):
    path = tmp_path / "demo.json"
    demo = DemoKeitaro(path, "test_borkoev_")
    before = path.read_bytes()
    with httpx.Client(
        base_url="https://demo.invalid/admin_api/v1/", transport=httpx.MockTransport(demo)
    ) as client:
        assert (
            client.put("streams/202", json={"offers": [{"offer_id": 11, "share": 25}]}).status_code
            == 422
        )
        assert client.get("offers/999").status_code == 404
    assert path.read_bytes() == before


def test_demo_disk_failure_does_not_publish_memory_only_changes(tmp_path, monkeypatch):
    demo = DemoKeitaro(tmp_path / "demo.json", "test_borkoev_")

    def fail(_state):
        raise OSError("disk full")

    monkeypatch.setattr(demo, "save", fail)
    with httpx.Client(
        base_url="https://demo.invalid/admin_api/v1/", transport=httpx.MockTransport(demo)
    ) as client:
        assert (
            client.put("streams/202", json={"offers": [{"offer_id": 14, "share": 100}]}).status_code
            == 500
        )
        assert client.get("streams/202").json()["offers"][0]["offer_id"] == 11


def test_demo_fault_loses_one_response_after_persisting_the_write(tmp_path):
    path = tmp_path / "demo.json"
    demo = DemoKeitaro(path, "test_borkoev_")
    demo.arm_fault("publish_timeout")
    with httpx.Client(
        base_url="https://demo.invalid/admin_api/v1/", transport=httpx.MockTransport(demo)
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.put(
                "streams/202", json={"offers": [{"offer_id": 14, "share": 100, "state": "active"}]}
            )
        assert client.get("streams/202").json()["offers"][0]["offer_id"] == 14
        assert (
            client.put(
                "streams/202", json={"offers": [{"offer_id": 11, "share": 100, "state": "active"}]}
            ).status_code
            == 200
        )
    assert DemoKeitaro(path, "test_borkoev_").state["streams"]["202"]["offers"][0]["offer_id"] == 11


def test_demo_api_recovers_applied_publication_after_backend_restart(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'adrobot.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("KEITARO_MODE", "demo")
    settings = Settings(database_url=database_url, keitaro_mode="demo", keitaro_api_key="")
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["mode"] == "demo"
        assert client.get("/api/catalogs").json()["mode"] == "demo"
        imported = client.post("/api/campaigns/101/fetch", json={}).json()
        stream = next(item for item in imported["streams"] if item["editable"])
        draft = client.patch(
            f"/api/streams/{stream['id']}/offers/11",
            json={"version": stream["version"], "weight": 25, "pinned": True},
        ).json()
        assert client.post("/api/demo/fault", json={"kind": "publish_timeout"}).status_code == 200
        published = client.post(
            f"/api/streams/{stream['id']}/publish",
            json={"version": draft["version"]},
            headers={"Idempotency-Key": "demo-restart-publication"},
        )
        assert published.status_code == 504
        operation_id = published.json()["operation_id"]
        assert operation_id and published.json()["outcome"] == "unknown"
    with TestClient(create_app(settings)) as restarted:
        checked = restarted.post(f"/api/operations/{operation_id}/reconcile", json={})
        assert checked.json()["status"] == "succeeded"
        final = restarted.get("/api/campaigns/101").json()
    restored = next(item for item in final["streams"] if item["editable"])
    assert restored["dirty"] is False and restored["operation"] is None
    assert [item["weight"] for item in restored["offers"]] == [25, 38, 37]
    assert restored["offers"][0]["pinned"] is True
    assert not (tmp_path / "adrobot.db").exists()


def test_fault_controls_cannot_affect_a_live_provider(system):
    client, remote, _app = system
    before = len(remote.calls)
    assert client.post("/api/demo/fault", json={"kind": "publish_timeout"}).status_code == 404
    assert len(remote.calls) == before
