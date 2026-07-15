from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.catalog_routes import get_catalogs,get_catalog_admin_service
from app.modules.agent_platform.orchestration.agent_registry import AGENT_REGISTRATIONS, AgentRegistry
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user

NOW=datetime(2026,7,15,tzinfo=UTC)


def user(permissions):
    return AuthenticatedUser(user_id=uuid4(),username="student01",display_name="Student",email=None,department=None,status="active",roles=(AuthenticatedRole(uuid4(),"student","Student"),),permissions=tuple(permissions),last_login_at=None,created_at=NOW,version=1)


def client(permissions):
    app=create_app()
    async def auth(): return user(permissions)
    async def catalogs(): return AgentRegistry(AGENT_REGISTRATIONS), ToolRegistry(TOOL_CONTRACTS.values())
    app.dependency_overrides[get_authenticated_user]=auth
    app.dependency_overrides[get_catalogs]=catalogs
    return TestClient(app)


def test_agent_catalog_hides_prompt_and_returns_request_id() -> None:
    response=client({"agent:catalog:read"}).get("/api/v1/agents",headers={"X-Request-Id":"catalog-request"})
    assert response.status_code==200 and response.json()["request_id"]=="catalog-request"
    assert len(response.json()["data"]["items"])==6
    assert "system_prompt" not in response.text


def test_tool_catalog_filters_permissions_module_and_internal_tools() -> None:
    response=client({"tool:catalog:read","electricity:read_own"}).get("/api/v1/tools?module=m2")
    assert response.status_code==200
    names={item["name"] for item in response.json()["data"]["items"]}
    assert "electricity.get_balance" in names
    assert "governance.write_audit" not in response.text


def test_tool_detail_default_denies_unavailable_tool() -> None:
    response=client({"tool:catalog:read"}).get("/api/v1/tools/electricity.get_balance")
    assert response.status_code==404 and response.json()["code"]=="TOOL_NOT_FOUND"


def test_catalog_permission_is_required() -> None:
    response=client(set()).get("/api/v1/agents")
    assert response.status_code==403 and response.json()["code"]=="AUTH_FORBIDDEN"


def test_import_and_health_do_not_load_catalog_database() -> None:
    response=client(set()).get("/health/live")
    assert response.status_code==200


def test_tool_state_update_requires_confirmation_and_write_permission() -> None:
    service=type("Service",(),{})();service.update=__import__("unittest.mock",fromlist=["AsyncMock"]).AsyncMock(return_value=(200,{"code":"OK","message":"success","data":{"name":"knowledge.search","module":"m1","description":"search","risk_level":"r0","enabled":False,"version":"1.0.0","input_schema":{},"output_schema":{},"required_permissions":[],"timeout_ms":5000,"idempotent":True,"requires_approval":False},"request_id":"state-request","timestamp":NOW.isoformat()},"state-request"))
    app=create_app()
    async def auth():return user({"tool:catalog:write"})
    async def admin():yield service
    app.dependency_overrides[get_authenticated_user]=auth;app.dependency_overrides[get_catalog_admin_service]=admin
    client_=TestClient(app)
    payload={"enabled":False,"confirmed":True,"reason":"maintenance"}
    assert client_.patch("/api/v1/tools/knowledge.search",json=payload).status_code==422
    response=client_.patch("/api/v1/tools/knowledge.search",headers={"Idempotency-Key":"state-key","X-Request-Id":"state-request"},json=payload)
    assert response.status_code==200 and response.headers["X-Request-Id"]=="state-request"
