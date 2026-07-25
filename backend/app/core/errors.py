import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.request_id import REQUEST_ID_HEADER
from app.shared.responses import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

HTTP_ERROR_DEFAULTS: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "请求无效"),
    401: ("AUTH_UNAUTHORIZED", "登录状态无效，请重新登录"),
    403: ("AUTH_FORBIDDEN", "没有执行该操作的权限"),
    404: ("NOT_FOUND", "请求的资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不受支持"),
    409: ("CONFLICT", "请求与资源当前状态冲突"),
    413: ("PAYLOAD_TOO_LARGE", "请求内容过大"),
    415: ("UNSUPPORTED_MEDIA_TYPE", "不支持的内容类型"),
    429: ("RATE_LIMITED", "请求过于频繁"),
}
LOCATION_PREFIXES = {"body", "query", "path", "header", "cookie"}


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Sequence[ErrorDetail] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = list(details or [])
        self.headers = dict(headers or {})


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid4().hex)


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Sequence[ErrorDetail] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    payload = ErrorResponse(
        code=code,
        message=message,
        details=list(details),
        request_id=request_id,
        timestamp=datetime.now(UTC),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


def _validation_field(location: Sequence[Any]) -> str | None:
    parts = list(location)
    if parts and parts[0] in LOCATION_PREFIXES:
        parts = parts[1:]
    return ".".join(str(part) for part in parts) or None


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = [
        ErrorDetail(
            field=_validation_field(error["loc"]),
            reason=error["msg"],
        )
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="请求参数校验失败",
        details=details,
    )


async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    code, message = HTTP_ERROR_DEFAULTS.get(
        exc.status_code,
        ("HTTP_ERROR", "请求处理失败"),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.exception(
        "Unhandled application error",
        extra={"request_id": request_id},
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="服务器内部错误",
    )


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(  # type: ignore[arg-type]
        RequestValidationError,
        validation_error_handler,
    )
    application.add_exception_handler(  # type: ignore[arg-type]
        StarletteHTTPException,
        http_error_handler,
    )
    application.add_exception_handler(Exception, unhandled_error_handler)
