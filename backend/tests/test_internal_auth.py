import asyncio
from unittest.mock import AsyncMock,MagicMock
from uuid import uuid4
import pytest
from app.core.config import Settings
from app.modules.agent_platform.internal_auth import InternalServiceNotConfigured,InternalServiceUnauthorized,InternalUserContextLoader,verify_internal_service_token

def settings(secret=None):
 return Settings(database_url="postgresql+asyncpg://u:p@localhost/db",redis_url="redis://localhost",jwt_secret="jwt",frontend_origin="http://localhost:5173",deepseek_api_key="deep",internal_tool_secret=secret)
def test_service_token_is_independent_and_safe():
 assert verify_internal_service_token("Bearer service-secret",settings("service-secret")).service=="agent_runtime"
 with pytest.raises(InternalServiceUnauthorized):verify_internal_service_token("Bearer wrong",settings("service-secret"))
 with pytest.raises(InternalServiceUnauthorized):verify_internal_service_token(None,settings("service-secret"))
 with pytest.raises(InternalServiceNotConfigured):verify_internal_service_token("Bearer any",settings())
 assert "service-secret" not in repr(settings("service-secret"))
def test_internal_user_context_reloads_current_roles_and_permissions():
 uid=uuid4();user=MagicMock(id=uid,username="student01",status="active");role=MagicMock(code="student")
 users=MagicMock();users.get_by_id=AsyncMock(return_value=user);rbac=MagicMock();rbac.list_roles_for_user=AsyncMock(return_value=[role]);rbac.list_permission_codes_for_user=AsyncMock(return_value=["agent:run"])
 context=asyncio.run(InternalUserContextLoader(users,rbac).load(uid,"internal-request"))
 assert context.user_id==uid and context.roles==("student",) and context.permissions==("agent:run",)
 user.status="disabled"
 with pytest.raises(InternalServiceUnauthorized):asyncio.run(InternalUserContextLoader(users,rbac).load(uid,"x"))
