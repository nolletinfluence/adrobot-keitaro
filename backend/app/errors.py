class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        operation_id: str | None = None,
        outcome: str | None = None,
    ):
        self.status = status
        self.code = code
        self.message = detail
        self.operation_id = operation_id
        self.outcome = outcome
        super().__init__(detail)
