import json
import logging
import re
from time import monotonic
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from app.errors import AppError

logger = logging.getLogger("adrobot.requests")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)
logger.propagate = False


def error_response(request: Request, error: AppError):
    request.state.error_code = error.code
    headers = {"X-Request-ID": request.state.request_id}
    if error.operation_id:
        headers["X-Operation-ID"] = error.operation_id
    return JSONResponse(
        status_code=error.status,
        content={
            "detail": error.message,
            "code": error.code,
            "operation_id": error.operation_id,
            "outcome": error.outcome,
            "request_id": request.state.request_id,
        },
        headers=headers,
    )


async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied) else uuid4().hex
    request.state.request_id = request_id
    started = monotonic()
    try:
        response = await call_next(request)
    except Exception:
        response = error_response(
            request, AppError(500, "INTERNAL_ERROR", "An unexpected server error occurred")
        )
    response.headers["X-Request-ID"] = request_id
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000),
                "operation_id": response.headers.get("X-Operation-ID"),
                "error_code": getattr(request.state, "error_code", None),
            },
            separators=(",", ":"),
        )
    )
    return response
