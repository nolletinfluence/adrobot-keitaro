import httpx
import pytest
from pydantic import ValidationError

from app import deadline
from app.config import Settings
from app.keitaro import KeitaroClient, UpstreamError


def test_sequential_requests_share_one_operation_budget(monkeypatch):
    clock = [10.0]
    timeouts = []
    monkeypatch.setattr(deadline, "monotonic", lambda: clock[0])

    def handle(request):
        timeouts.append(request.extensions["timeout"]["read"])
        clock[0] += 3
        return httpx.Response(200, json={"ok": True})

    client = KeitaroClient(Settings(keitaro_api_key="test-key"), httpx.MockTransport(handle))
    token = deadline.operation_deadline.set(15.0)
    try:
        assert client.get("first") == {"ok": True}
        assert client.get("second") == {"ok": True}
        with pytest.raises(UpstreamError) as result:
            client.post("third", {})
        assert result.value.status == 504
        assert result.value.ambiguous is False
        assert timeouts == [5.0, 2.0]
    finally:
        deadline.operation_deadline.reset(token)
        client.close()


def test_write_timeout_is_ambiguous_and_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(deadline, "monotonic", lambda: 10.0)

    def handle(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 1.0
        raise httpx.ReadTimeout("timed out", request=request)

    client = KeitaroClient(Settings(keitaro_api_key="test-key"), httpx.MockTransport(handle))
    token = deadline.operation_deadline.set(11.0)
    try:
        with pytest.raises(UpstreamError) as result:
            client.post("campaigns", {"name": "test_borkoev_timeout"})
        assert result.value.status == 504
        assert result.value.ambiguous is True
        assert len(calls) == 1
    finally:
        deadline.operation_deadline.reset(token)
        client.close()


def test_operation_timeout_cannot_consume_frontend_response_margin():
    with pytest.raises(ValidationError):
        Settings(upstream_operation_timeout_seconds=51)
