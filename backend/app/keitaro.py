from typing import Any

import httpx

from app.config import Settings
from app.deadline import remaining_timeout
from app.errors import AppError


class UpstreamError(AppError):
    def __init__(
        self, status: int, message: str, ambiguous: bool = False, code: str = "UPSTREAM_REJECTED"
    ):
        self.ambiguous = ambiguous
        super().__init__(status, code, message, outcome="unknown" if ambiguous else "not_applied")


class KeitaroClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.configured = bool(settings.keitaro_api_key.get_secret_value())
        self.per_call_timeout = settings.upstream_timeout_seconds
        self.client = httpx.Client(
            base_url=settings.keitaro_base_url.rstrip("/") + "/",
            headers={"Api-Key": settings.keitaro_api_key.get_secret_value()},
            timeout=settings.upstream_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, payload: dict | None = None) -> Any:
        if not self.configured:
            raise UpstreamError(
                503,
                "Keitaro API key is not configured on the server",
                code="UPSTREAM_NOT_CONFIGURED",
            )
        writing = method not in {"GET", "HEAD"}
        timeout = remaining_timeout(self.per_call_timeout)
        if timeout <= 0:
            raise UpstreamError(
                504, "The Keitaro operation time limit was reached", code="UPSTREAM_DEADLINE"
            )
        try:
            response = self.client.request(method, path.lstrip("/"), json=payload, timeout=timeout)
        except httpx.TimeoutException as error:
            suffix = "; check Keitaro before retrying this write" if writing else ""
            raise UpstreamError(
                504, "Keitaro request timed out" + suffix, writing, "UPSTREAM_TIMEOUT"
            ) from error
        except httpx.RequestError as error:
            suffix = "; check Keitaro before retrying this write" if writing else ""
            raise UpstreamError(
                502, "Keitaro connection failed" + suffix, writing, "UPSTREAM_UNAVAILABLE"
            ) from error
        if response.status_code >= 400:
            if response.status_code == 404:
                raise UpstreamError(
                    404, "The requested entity was not found in Keitaro", code="UPSTREAM_NOT_FOUND"
                )
            if response.status_code in {401, 403}:
                raise UpstreamError(
                    502,
                    "Keitaro rejected the server API credentials or permissions",
                    code="UPSTREAM_AUTH",
                )
            raise UpstreamError(
                502,
                f"Keitaro rejected the request (HTTP {response.status_code})",
                writing and response.status_code >= 500,
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise UpstreamError(
                502, "Keitaro returned an invalid response", writing, "UPSTREAM_INVALID_RESPONSE"
            ) from error

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, payload: dict):
        return self.request("POST", path, payload)

    def put(self, path: str, payload: dict):
        return self.request("PUT", path, payload)

    def delete(self, path: str):
        return self.request("DELETE", path)
