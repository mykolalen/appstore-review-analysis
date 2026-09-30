"""Shared domain/API error schema and exception handlers."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Expected application error with a stable machine-readable code."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retry_after = retry_after


class ErrorBody(BaseModel):
    """Machine-readable error payload."""

    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Top-level API error envelope."""

    error: ErrorBody


def request_id_from(request: Request) -> str:
    """Read the request id assigned by middleware, with a safe fallback."""

    return str(getattr(request.state, "request_id", "unknown"))


def error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    """Create one consistent API error response."""

    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id_from(request),
            details=dict(details or {}),
        )
    )
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register the error contract used by the service."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            retry_after=exc.retry_after,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            code = "NOT_FOUND"
            message = "Resource not found."
        else:
            code = "HTTP_ERROR"
            message = str(exc.detail)
        return error_response(
            request=request,
            status_code=exc.status_code,
            code=code,
            message=message,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            "errors": [
                {
                    "type": item["type"],
                    "loc": list(item["loc"]),
                    "msg": item["msg"],
                }
                for item in exc.errors()
            ]
        }
        return error_response(
            request=request,
            status_code=422,
            code="INVALID_INPUT",
            message="Request validation failed.",
            details=details,
        )
