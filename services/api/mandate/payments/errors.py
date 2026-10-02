"""The contract's error envelope (contracts/README.md section 9).

Business refusals are not errors: they are HTTP 200 decisions. These are for
authentication, scope, missing resources, conflicts, bad input and contention.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mandate.storage.db import DatabaseBusy


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def unauthenticated(message: str = "Missing or invalid bearer token.") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message)


def forbidden(message: str = "This credential cannot perform this operation.") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} not found or not visible to caller.")


def conflict(message: str, code: str = "STATE_CONFLICT", **details) -> ApiError:
    return ApiError(409, code, message, details=details)


def invalid(message: str, **details) -> ApiError:
    return ApiError(422, "INVALID_REQUEST", message, details=details)


def busy() -> ApiError:
    return ApiError(503, "SERVICE_BUSY", "Wallet ledger is busy; no payment was performed. Retry with the same Idempotency-Key.", retryable=True)


def envelope(code: str, message: str, *, retryable: bool = False, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": f"req_{uuid.uuid4().hex}",
            "retryable": retryable,
            "details": details or {},
        }
    }


_STATUS_CODES = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "INVALID_REQUEST", 409: "STATE_CONFLICT", 422: "INVALID_REQUEST", 503: "SERVICE_BUSY"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(envelope(exc.code, exc.message, retryable=exc.retryable, details=exc.details), status_code=exc.status)

    @app.exception_handler(DatabaseBusy)
    async def _busy(_: Request, exc: DatabaseBusy):
        err = busy()
        return JSONResponse(envelope(err.code, err.message, retryable=True), status_code=503)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        problems = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(envelope("INVALID_REQUEST", "Request does not match the contract.", details={"errors": problems}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "INTERNAL_ERROR")
        return JSONResponse(envelope(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        return JSONResponse(envelope("INTERNAL_ERROR", "Unexpected server error; no payment was performed."), status_code=500)
