import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

import httpx


class DemoKeitaro:
    def __init__(self, state_path: Path, prefix: str):
        self.state_path = state_path
        self.lock = RLock()
        self.fault: str | None = None
        if state_path.exists():
            self.state = json.loads(state_path.read_text())
            if self.state.get("version") != 1:
                raise ValueError("Unsupported demo data version")
        else:
            self.state = self.seed(prefix)
            self.save(self.state)

    @staticmethod
    def seed(prefix: str) -> dict:
        return {
            "version": 1,
            "offers": {
                str(ident): {"id": ident, "name": name, "state": "active"}
                for ident, name in [
                    (11, "Основной оффер"),
                    (12, "Альтернативный оффер"),
                    (13, "Резервный оффер"),
                    (14, "Новый оффер"),
                ]
            },
            "campaigns": {
                "101": {
                    "id": 101,
                    "name": prefix + "demo",
                    "alias": "demo",
                    "domain": "demo.invalid",
                    "domain_id": 1,
                    "group_id": 2,
                    "traffic_source_id": 3,
                    "state": "active",
                    "type": "position",
                }
            },
            "streams": {
                "201": {
                    "id": 201,
                    "campaign_id": 101,
                    "name": "Страна KG → Google",
                    "schema": "redirect",
                    "type": "regular",
                    "position": 1,
                    "state": "active",
                    "action_type": "http",
                    "action_payload": "https://google.com",
                    "filters": [{"name": "country", "mode": "accept", "payload": ["KG"]}],
                    "offers": [],
                },
                "202": {
                    "id": 202,
                    "campaign_id": 101,
                    "name": "Распределение офферов",
                    "schema": "landings",
                    "type": "default",
                    "position": 2,
                    "state": "active",
                    "filters": [],
                    "landings": [],
                    "offers": [
                        {"offer_id": 11, "share": 34, "state": "active"},
                        {"offer_id": 12, "share": 33, "state": "active"},
                        {"offer_id": 13, "share": 33, "state": "active"},
                    ],
                },
            },
        }

    def save(self, state: dict):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.state_path.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump(state, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def arm_fault(self, kind: str):
        if kind not in {"publish_timeout", "create_timeout"}:
            raise ValueError("Unknown demo fault")
        with self.lock:
            self.fault = kind

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            state = deepcopy(self.state)
            try:
                response, changed = self.dispatch(request, state)
                if changed and response.is_success:
                    self.save(state)
                    self.state = state
                    path = request.url.path.removeprefix("/admin_api/v1/").strip("/")
                    matching = (
                        self.fault == "publish_timeout"
                        and request.method == "PUT"
                        and path.startswith("streams/")
                    ) or (
                        self.fault == "create_timeout"
                        and request.method == "POST"
                        and path == "campaigns"
                    )
                    if matching:
                        self.fault = None
                        raise httpx.ReadTimeout(
                            "Simulated lost response after applying demo write", request=request
                        )
                return response
            except (ValueError, TypeError, KeyError):
                return httpx.Response(422, json={"error": "Invalid demo API request"})
            except OSError:
                return httpx.Response(500, json={"error": "Demo state could not be saved"})

    @staticmethod
    def dispatch(request: httpx.Request, state: dict) -> tuple[httpx.Response, bool]:
        parts = request.url.path.removeprefix("/admin_api/v1/").strip("/").split("/")
        method = request.method
        resource = parts[0]
        payload = json.loads(request.content) if request.content else {}

        def answer(value, status=200, changed=False):
            return httpx.Response(status, json=value), changed

        if method == "GET":
            catalogs = {
                "domains": [{"id": 1, "name": "Демо-домен"}],
                "groups": [{"id": 2, "name": "Демонстрационные кампании"}],
                "traffic_sources": [{"id": 3, "name": "Демо-источник"}],
            }
            if resource in catalogs:
                return answer(catalogs[resource])
            if resource in state and isinstance(state[resource], dict):
                rows = state[resource]
                if len(parts) == 1:
                    return answer(list(rows.values()))
                row = rows.get(parts[1])
                if row is None:
                    return answer({"error": "Not found"}, 404)
                if resource == "campaigns" and len(parts) == 3 and parts[2] == "streams":
                    streams = [
                        item
                        for item in state["streams"].values()
                        if item["campaign_id"] == row["id"] and item.get("state") != "deleted"
                    ]
                    return answer(sorted(streams, key=lambda item: item.get("position", 0)))
                return answer(row)
        if method == "POST" and resource == "campaigns" and len(parts) == 1:
            alias = payload.get("alias", "")
            if not alias or not alias.isalnum() or not payload.get("name"):
                return answer({"error": "Name and alphanumeric alias are required"}, 422)
            if any(item["alias"] == alias for item in state["campaigns"].values()):
                return answer({"error": "Alias already exists"}, 422)
            ident = max(map(int, state["campaigns"]), default=100) + 1
            row = {**payload, "id": ident, "domain": "demo.invalid"}
            state["campaigns"][str(ident)] = row
            return answer(row, changed=True)
        if method == "POST" and resource == "streams" and len(parts) == 1:
            if str(payload["campaign_id"]) not in state["campaigns"]:
                return answer({"error": "Campaign not found"}, 404)
            ident = max(map(int, state["streams"]), default=200) + 1
            row = {"filters": [], "offers": [], "landings": [], **payload, "id": ident}
            state["streams"][str(ident)] = row
            return answer(row, changed=True)
        if method == "PUT" and resource == "streams" and len(parts) == 2:
            row = state["streams"].get(parts[1])
            if row is None:
                return answer({"error": "Stream not found"}, 404)
            offers = payload.get("offers", row.get("offers", []))
            if len({item["offer_id"] for item in offers}) != len(offers):
                return answer({"error": "Duplicate offer"}, 422)
            if any(
                str(item["offer_id"]) not in state["offers"]
                or type(item["share"]) is not int
                or not 0 <= item["share"] <= 100
                for item in offers
            ):
                return answer({"error": "Invalid offer allocation"}, 422)
            if (
                offers
                and sum(item["share"] for item in offers if item.get("state", "active") == "active")
                != 100
            ):
                return answer({"error": "Shares must total 100"}, 422)
            row.update(payload)
            return answer(row, changed=True)
        if method == "DELETE" and resource == "campaigns" and len(parts) == 2:
            row = state["campaigns"].get(parts[1])
            if row is None:
                return answer({"error": "Campaign not found"}, 404)
            row["state"] = "deleted"
            return httpx.Response(204), True
        return answer({"error": "Unsupported demo endpoint"}, 404)
