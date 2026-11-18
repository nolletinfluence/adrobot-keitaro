import json
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.database import create_database
from app.main import create_app


def test_operations_migration_preserves_legacy_pinned_draft_and_archive(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("KEITARO_MODE", "live")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "0001_initial")
    published = [
        {"offer_id": 11, "weight": 100, "pinned": False, "removed": False, "position": 0},
        {"offer_id": 12, "weight": 0, "pinned": False, "removed": True, "position": 1},
    ]
    engine, _sessions = create_database(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO campaigns (id,name,tracking_url) VALUES (101,'test_borkoev_legacy',NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO offers (id,name) VALUES (11,'Original'),(12,'Archived')")
        )
        connection.execute(
            text(
                "INSERT INTO streams (id,campaign_id,name,position,editable,version,remote_offers,published_state,synced_at) VALUES (202,101,'Offers',2,1,7,:remote,:published,NULL)"
            ),
            {
                "remote": json.dumps([{"offer_id": 11, "share": 100, "state": "active"}]),
                "published": json.dumps(published),
            },
        )
        connection.execute(
            text(
                "INSERT INTO stream_offers (stream_id,offer_id,position,weight,pinned,removed) VALUES (202,11,0,25,1,0),(202,12,1,75,0,0)"
            )
        )
    engine.dispose()
    settings = Settings(
        database_url=url, keitaro_base_url="https://keitaro.example", keitaro_api_key="test-key"
    )

    def assert_preserved():
        application = create_app(
            settings, httpx.MockTransport(lambda _request: httpx.Response(500))
        )
        with TestClient(application) as client:
            response = client.get("/api/campaigns/101")
            assert response.status_code == 200, response.text
            stream = response.json()["streams"][0]
            assert stream["version"] == 7 and stream["dirty"] is True
            assert stream["operation"] is None
            assert [(row["id"], row["weight"], row["pinned"]) for row in stream["offers"]] == [
                (11, 25, True),
                (12, 75, False),
            ]
            assert stream["published_offers"][1]["removed"] is True
            assert stream["published_offers"][1]["weight"] == 0

    command.upgrade(config, "head")
    assert_preserved()
    command.downgrade(config, "0001_initial")
    command.upgrade(config, "head")
    assert_preserved()
