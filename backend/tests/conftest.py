from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeKeitaro:
    def __init__(self):
        self.offers = {
            number: {"id": number, "name": f"Offer {number}", "state": "active"}
            for number in (11, 12, 13)
        }
        self.campaigns = {
            101: {
                "id": 101,
                "name": "test_borkoev_existing",
                "domain": "tracker.example",
                "alias": "example",
                "state": "active",
            }
        }
        self.streams = {
            201: {
                "id": 201,
                "campaign_id": 101,
                "name": "Google",
                "schema": "redirect",
                "type": "regular",
                "state": "active",
                "position": 1,
                "action_payload": "https://google.com",
                "filters": [{"name": "country", "mode": "accept", "payload": ["KG"]}],
                "offers": [],
            },
            202: {
                "id": 202,
                "campaign_id": 101,
                "name": "Offers",
                "schema": "landings",
                "type": "default",
                "state": "active",
                "position": 2,
                "filters": [{"name": "device", "payload": ["mobile"]}],
                "landings": [{"landing_id": 99, "share": 100}],
                "offers": [{"offer_id": 11, "share": 100, "state": "active"}],
            },
        }
        self.calls = []
        self.failure = None
        self.after_failure = None
        self.before_request = None
        self.after_request = None
        self.reverse_offer_responses = False

    def handle(self, request: httpx.Request):
        response = self._handle(request)
        path = request.url.path.removeprefix("/admin_api/v1/")
        if self.after_request:
            self.after_request(request.method, path, response)
        if self.after_failure and (request.method, path) == self.after_failure[:2]:
            failure = self.after_failure
            self.after_failure = None
            if failure[2] == "timeout":
                raise httpx.ReadTimeout("response lost after applying write", request=request)
            return httpx.Response(failure[2], json={"error": "response lost after applying write"})
        if self.reverse_offer_responses and response.is_success and response.content:
            value = response.json()
            rows = value if isinstance(value, list) else [value]
            for row in rows:
                if isinstance(row, dict) and "offers" in row:
                    row["offers"] = list(reversed(row["offers"]))
            response = httpx.Response(response.status_code, json=value)
        return response

    def _handle(self, request: httpx.Request):
        import json

        assert request.headers["Api-Key"] == "test-key"
        method = request.method
        path = request.url.path.removeprefix("/admin_api/v1/")
        payload = json.loads(request.content) if request.content else None
        self.calls.append((method, path, deepcopy(payload)))
        if self.before_request:
            self.before_request(method, path, payload)
        if self.failure and (method, path) == self.failure[:2]:
            failure = self.failure
            self.failure = None
            if failure[2] == "timeout":
                raise httpx.ReadTimeout("simulated timeout", request=request)
            return httpx.Response(failure[2], json={"error": "upstream internals test-key"})
        if method == "GET":
            if path == "offers":
                return httpx.Response(200, json=list(self.offers.values()))
            if path.startswith("offers/"):
                return httpx.Response(200, json=self.offers[int(path.split("/")[1])])
            if path == "campaigns":
                return httpx.Response(200, json=list(self.campaigns.values()))
            if path == "domains":
                return httpx.Response(200, json=[{"id": 1, "name": "tracker.example"}])
            if path == "groups":
                assert request.url.params["type"] == "campaigns"
                return httpx.Response(200, json=[{"id": 2, "name": "Test group"}])
            if path == "traffic_sources":
                return httpx.Response(200, json=[{"id": 3, "name": "Test source"}])
            if path.startswith("campaigns/"):
                campaign_id = int(path.split("/")[1])
                if path.endswith("/streams"):
                    rows = [
                        stream
                        for stream in self.streams.values()
                        if stream["campaign_id"] == campaign_id
                    ]
                    return httpx.Response(200, json=rows)
                return httpx.Response(200, json=self.campaigns[campaign_id])
            if path.startswith("streams/"):
                return httpx.Response(200, json=self.streams[int(path.split("/")[1])])
        if method == "PUT" and path.startswith("streams/"):
            stream = self.streams[int(path.split("/")[1])]
            stream.update(payload)
            return httpx.Response(200, json=stream)
        if method == "POST" and path == "campaigns":
            alias = payload.get("alias")
            if not isinstance(alias, str) or not alias or not alias.isalnum():
                return httpx.Response(422, json={"alias": ["A valid alias is required"]})
            if any(row["alias"] == alias for row in self.campaigns.values()):
                return httpx.Response(422, json={"alias": ["Alias must be unique"]})
            campaign_id = max(self.campaigns) + 1
            row = {**payload, "id": campaign_id, "domain": "tracker.example"}
            self.campaigns[campaign_id] = row
            return httpx.Response(200, json=row)
        if method == "POST" and path == "streams":
            stream_id = max(self.streams) + 1
            row = {**payload, "id": stream_id}
            self.streams[stream_id] = row
            return httpx.Response(200, json=row)
        if method == "DELETE" and path.startswith("campaigns/"):
            campaign_id = int(path.split("/")[1])
            self.campaigns[campaign_id]["state"] = "deleted"
            return httpx.Response(204)
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def system(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'adrobot.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("KEITARO_MODE", "live")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    settings = Settings(
        database_url=database_url,
        keitaro_base_url="https://keitaro.example",
        keitaro_api_key="test-key",
    )
    remote = FakeKeitaro()
    application = create_app(settings, httpx.MockTransport(remote.handle))
    with TestClient(application) as client:
        yield client, remote, application
