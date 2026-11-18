from contextvars import ContextVar
from time import monotonic

operation_deadline: ContextVar[float | None] = ContextVar("operation_deadline", default=None)


def remaining_timeout(per_call_timeout: float) -> float:
    deadline = operation_deadline.get()
    if deadline is None:
        return per_call_timeout
    return min(per_call_timeout, deadline - monotonic())
