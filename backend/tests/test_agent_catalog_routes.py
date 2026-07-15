from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.catalog_routes import get_catalogs
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
