from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
MIN_REQUEST_ID_LENGTH = 8
MAX_REQUEST_ID_LENGTH = 64


def _is_valid_request_id(value: str | None) -> bool:
    return value is not None and MIN_REQUEST_ID_LENGTH <= len(value) <= MAX_REQUEST_ID_LENGTH


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        supplied_request_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            supplied_request_id
            if _is_valid_request_id(supplied_request_id)
            else uuid4().hex
        )
        request.state.request_id = request_id

        response = await call_next(request)
        # Internal replay responses may intentionally preserve the original
        # request id stored with the idempotent result.  Keep that value;
        # otherwise attach the id generated for this request.
        if REQUEST_ID_HEADER not in response.headers:
            response.headers[REQUEST_ID_HEADER] = request_id
        return response
