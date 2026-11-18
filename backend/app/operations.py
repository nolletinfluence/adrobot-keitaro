import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.errors import AppError
from app.models import Operation
from app.schemas import OperationView


def now():
    return datetime.now(timezone.utc)


def timestamp(value: datetime):
    return (
        value.replace(tzinfo=timezone.utc).isoformat()
        if value.tzinfo is None
        else value.isoformat()
    )


def operation_view(operation: Operation) -> OperationView:
    return OperationView(
        id=operation.id,
        kind=operation.kind,
        status=operation.status,
        phase=operation.phase,
        campaign_id=operation.campaign_id,
        stream_id=operation.stream_id,
        detail=operation.detail,
        error_code=operation.error_code,
        created_at=timestamp(operation.created_at),
        updated_at=timestamp(operation.updated_at),
    )


def normalize_key(key: str | None) -> str:
    if key is None:
        return uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
        raise AppError(422, "INVALID_IDEMPOTENCY_KEY", "Use a bounded ASCII idempotency key")
    return key


def request_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def find_operation(session, kind: str, key: str, payload: dict, target: dict):
    operation = session.scalar(
        select(Operation).where(Operation.kind == kind, Operation.idempotency_key == key)
    )
    if operation and operation.context_json.get("target") != target:
        raise AppError(
            409,
            "OPERATION_TARGET_MISMATCH",
            "This operation belongs to another Keitaro target",
            operation.id,
            "not_applied",
        )
    if operation and operation.request_hash != request_hash(payload):
        raise AppError(
            409,
            "IDEMPOTENCY_KEY_CONFLICT",
            "This key belongs to a different request",
            operation.id,
            "not_applied",
        )
    return operation


def prepare_operation(session, kind: str, key: str, payload: dict, context: dict, **ids):
    operation = Operation(
        id=uuid4().hex,
        kind=kind,
        idempotency_key=key,
        request_hash=request_hash(payload),
        status="pending",
        phase="prepared",
        context_json=context,
        created_at=now(),
        updated_at=now(),
        **ids,
    )
    session.add(operation)
    session.flush()
    return operation


def reject_unfinished(operation: Operation):
    if operation.status in {"pending", "uncertain"}:
        raise AppError(
            409,
            "OPERATION_RECONCILIATION_REQUIRED",
            "Inspect and reconcile the existing operation before starting another write",
            operation.id,
            "unknown",
        )
    raise AppError(
        operation.error_status or 409,
        operation.error_code or "OPERATION_FAILED",
        operation.detail or "This operation did not complete",
        operation.id,
        "not_applied" if operation.status == "failed" else "unknown",
    )
