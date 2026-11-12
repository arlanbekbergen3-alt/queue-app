class AppError(Exception):
    status_code = 500
    detail = "Internal server error"
    code = "internal_error"
    headers: dict[str, str] | None = None

    def __init__(
        self,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        code: str | None = None,
    ):
        self.detail = detail or self.detail
        if code is not None:
            self.code = code
        if headers is not None:
            self.headers = headers
        super().__init__(self.detail)


class NotFoundError(AppError):
    status_code = 404
    detail = "Not found"
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    detail = "Conflict"
    code = "conflict"


class UnprocessableError(AppError):
    status_code = 422
    detail = "Unprocessable"
    code = "unprocessable"


class ForbiddenError(AppError):
    status_code = 403
    detail = "Forbidden"
    code = "forbidden"


class UnauthorizedError(AppError):
    status_code = 401
    detail = "Unauthorized"
    code = "unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}


class OverloadedError(AppError):
    status_code = 503
    detail = "Service is overloaded, retry later"
    code = "overloaded"
    headers = {"Retry-After": "2"}


class RateLimitError(AppError):
    status_code = 429
    detail = "Too many requests"
    code = "rate_limited"


class ServiceUnavailableError(AppError):
    status_code = 503
    detail = "Service unavailable"
    code = "service_unavailable"
