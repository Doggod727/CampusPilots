from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.evaluation_routes import get_evaluation_service
from app.modules.agent_platform.evaluations import (
    EvaluationComparisonData,
    EvaluationComparisonRow,
    EvaluationData,
    EvaluationPageData,
)
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse


NOW = datetime(2026, 7, 15, tzinfo=UTC)
USER_ID = uuid4()
EVALUATION_ID = uuid4()


def actor(permissions) -> AuthenticatedUser:
    return AuthenticatedUser(
        USER_ID, "model01", "Model", None, None, "active",
        (AuthenticatedRole(uuid4(), "model_engineer", "Model Engineer"),),
        tuple(permissions), None, NOW, 1,
    )


def evaluation() -> EvaluationData:
    return EvaluationData(
        id=EVALUATION_ID, target_type="model", target_id=uuid4(), status="queued",
        config={"seed": 42}, summary={}, metrics=(), report_key=None, error_code=None,
        created_at=NOW, finished_at=None,
    )


def client(service, permissions) -> TestClient:
    application = create_app()

    async def authenticated():
        return actor(permissions)

    async def evaluation_service():
        yield service

    application.dependency_overrides[get_authenticated_user] = authenticated
    application.dependency_overrides[get_evaluation_service] = evaluation_service
    return TestClient(application)


def test_list_detail_and_compare_use_read_permission_and_request_id():
    service = MagicMock()
    service.list = AsyncMock(return_value=EvaluationPageData(
        items=(evaluation(),),
        pagination=PageMetaData(page=1, page_size=20, total=1, total_pages=1),
    ))
    service.detail = AsyncMock(return_value=evaluation())
    service.compare = AsyncMock(return_value=EvaluationComparisonData(
        evaluation_ids=(EVALUATION_ID, uuid4()),
        metric_names=("accuracy",),
        rows=(EvaluationComparisonRow(evaluation_id=EVALUATION_ID, metrics={"accuracy": 0.9}),),
    ))
    allowed = client(service, {"evaluation:read"})
    response = allowed.get("/api/v1/evaluations", headers={"X-Request-Id": "evaluation-list"})
    assert response.status_code == 200
    assert response.json()["request_id"] == "evaluation-list"
    assert allowed.get(f"/api/v1/evaluations/{EVALUATION_ID}").status_code == 200
    second = uuid4()
    assert allowed.post("/api/v1/evaluations/compare", json={"evaluation_ids": [str(EVALUATION_ID), str(second)]}).status_code == 200
    assert client(service, set()).get("/api/v1/evaluations").status_code == 403


def test_create_requires_write_permission_idempotency_and_strict_payload():
    service = MagicMock()
    body = SuccessResponse(data=evaluation(), request_id="evaluation-create", timestamp=NOW).model_dump(mode="json")
    service.create = AsyncMock(return_value=(202, body, "evaluation-create"))
    allowed = client(service, {"evaluation:run"})
    payload = {"target_type": "model", "target_id": str(uuid4()), "config": {"seed": 42}}
    assert allowed.post("/api/v1/evaluations", json=payload).status_code == 422
    response = allowed.post(
        "/api/v1/evaluations",
        headers={"Idempotency-Key": "evaluation-key", "X-Request-Id": "evaluation-create"},
        json=payload,
    )
    assert response.status_code == 202
    assert response.headers["X-Request-Id"] == "evaluation-create"
    assert client(service, set()).post("/api/v1/evaluations", headers={"Idempotency-Key": "x"}, json=payload).status_code == 403
    assert allowed.post("/api/v1/evaluations", headers={"Idempotency-Key": "x"}, json={**payload, "unknown": True}).status_code == 422


def test_compare_rejects_duplicates_and_invalid_cardinality():
    service = MagicMock()
    allowed = client(service, {"evaluation:read"})
    one = str(uuid4())
    assert allowed.post("/api/v1/evaluations/compare", json={"evaluation_ids": [one]}).status_code == 422
    assert allowed.post("/api/v1/evaluations/compare", json={"evaluation_ids": [one, one]}).status_code == 422
    service.compare.assert_not_called()
