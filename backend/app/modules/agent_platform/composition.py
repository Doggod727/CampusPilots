from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.agent_platform.approvals import ApprovalRepository, ApprovalService, DatabaseApprovalVerifier
from app.modules.agent_platform.catalog_persistence import CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.checkpointing import DatabaseRuntimeCheckpointStore, PersistentRuntimeEventSink
from app.modules.agent_platform.deepseek import DeepSeekGateway, DeepSeekRouterAdapter, DeepSeekSpecialistProvider
from app.modules.agent_platform.internal_auth import InternalUserContextLoader
from app.modules.agent_platform.models import AgentRun
from app.modules.agent_platform.orchestration.runtime import BoundedGraphRuntime, DeterministicMockSpecialist
from app.modules.agent_platform.orchestration.router import RouterService
from app.modules.agent_platform.orchestration.supervisor import SupervisorPlanner
from app.modules.agent_platform.runtime_persistence import RuntimeCheckpointRepository, RuntimeEventRepository
from app.modules.agent_platform.runtime_worker import GraphRuntimeCommandProcessor
from app.modules.agent_platform.tool_gateway.electricity_adapters import ElectricityBalanceToolHandler, ElectricityTopupToolHandler
from app.modules.agent_platform.tool_gateway.campus_service_adapters import (
    ServiceGuideToolHandler,
    WorkOrderCreateToolHandler,
    WorkOrderGetToolHandler,
)
from app.modules.agent_platform.tool_gateway.executor import ToolExecutor
from app.modules.agent_platform.tool_gateway.governance_adapters import (
    GovernanceAuthorizeToolHandler, GovernanceCheckContentHandler, GovernanceWriteAuditHandler,
    M4AuditAdapter, M4ContentSafetyAdapter, M4ToolAuthorizationAdapter,
)
from app.modules.agent_platform.tool_gateway.mocks import build_mock_handlers
from app.modules.agent_platform.traces import TraceRepository, TraceService
from app.modules.campus_service.electricity import ElectricityService
from app.modules.campus_service.guides import ServiceGuideService
from app.modules.campus_service.repositories import (
    CampusReferenceRepository,
    ElectricityRepository,
    GuideRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_access import WorkOrderScopeRepository
from app.modules.campus_service.work_orders import WorkOrderService
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.audit import AuditService, redact
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository, IdempotencyRecordRepository, ModerationCaseRepository,
    RbacRepository, SensitiveWordRepository, UserRepository,
)


class M4AgentSafetyAdapter:
    def __init__(self, moderation: ModerationService) -> None:
        self._moderation = moderation

    async def check_input(self, user, text: str, context: Mapping):
        result = await self._moderation.scan(scope="agent_context", text=text)
        if result.action in {"review", "block"}:
            from app.modules.agent_platform.tool_gateway.errors import ToolForbidden
            raise ToolForbidden()
        return result.sanitized_text, redact(dict(context)) or {}

    async def check_output(self, user, result):
        scanned = await self._moderation.scan(scope="agent_context", text=result.summary)
        if scanned.action in {"review", "block"}:
            return result.model_copy(update={"status": "failed", "summary": "输出未通过安全策略", "structured_output": {}})
        return result.model_copy(update={"summary": scanned.sanitized_text})


class RuntimeStartContextLoader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = InternalUserContextLoader(UserRepository(session), RbacRepository(session))
        self._electricity = ElectricityRepository(session)

    async def load(self, run_id: UUID):
        run = (await self._session.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one_or_none()
        if run is None:
            from app.modules.agent_platform.traces import AgentRunNotFound
            raise AgentRunNotFound()
        user = await self._users.load(run.user_id, run.client_request_id)
        room_ids = await self._electricity.list_room_ids_for_user(user.user_id)
        return user.model_copy(update={"room_ids": room_ids}), run.input_summary, {}


class RuntimeCompositionFactory:
    """The only production assembler for M5 runtime components."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def load_catalogs(self, session: AsyncSession):
        return await PersistentCatalogLoader(CatalogRepository(session)).load()

    async def build_tool_executor(self, session: AsyncSession, catalogs=None):
        agents, tools = catalogs or await self.load_catalogs(session)
        audit = AuditService(AuditLogRepository(session))
        moderation = ModerationService(
            session=session,
            scanner=SensitiveWordScanner(SensitiveWordRepository(session)),
            repository=ModerationCaseRepository(session),
            audit_service=audit,
        )
        approval = ApprovalService(
            ApprovalRepository(session), ttl_seconds=self.settings.approval_ttl_seconds
        )
        authorization = M4ToolAuthorizationAdapter()
        electricity_repository = ElectricityRepository(session)
        electricity = ElectricityService(electricity_repository)
        guides = ServiceGuideService(GuideRepository(session))
        work_orders = WorkOrderService(
            session=session,
            campuses=CampusReferenceRepository(session),
            work_orders=WorkOrderRepository(session),
            events=WorkOrderEventRepository(session),
            idempotency=IdempotencyService(
                session=session,
                repository=IdempotencyRecordRepository(session),
            ),
            audit=audit,
            scopes=WorkOrderScopeRepository(session),
            rooms=electricity_repository,
        )
        handlers = build_mock_handlers()
        handlers.update({
            "service.get_guide": ServiceGuideToolHandler(guides),
            "work_order.create": WorkOrderCreateToolHandler(work_orders),
            "work_order.get": WorkOrderGetToolHandler(work_orders),
            "electricity.get_balance": ElectricityBalanceToolHandler(electricity),
            "electricity.create_topup_request": ElectricityTopupToolHandler(electricity),
            "governance.check_content": GovernanceCheckContentHandler(moderation),
            "governance.authorize_tool": GovernanceAuthorizeToolHandler(
                registry=tools,
                authorization=authorization,
                agent_allowlists={item.definition.code: item.version.tool_allowlist for item in agents.list_active()},
            ),
            "governance.write_audit": GovernanceWriteAuditHandler(audit),
        })
        executor = ToolExecutor(
            registry=tools,
            handlers=handlers,
            content_safety=M4ContentSafetyAdapter(moderation),
            authorization=authorization,
            approval_verifier=DatabaseApprovalVerifier(approval),
            audit=M4AuditAdapter(audit),
        )
        return executor, approval, moderation

    async def build_graph_runtime(self, session: AsyncSession) -> BoundedGraphRuntime:
        agents, tools = await self.load_catalogs(session)
        executor, approvals, moderation = await self.build_tool_executor(session, (agents, tools))
        gateway = DeepSeekGateway(
            api_key=self.settings.deepseek_api_key.get_secret_value(),
            base_url=str(self.settings.deepseek_base_url),
            model=self.settings.deepseek_model,
            timeout_seconds=self.settings.agent_run_timeout_seconds,
        )
        deepseek = DeepSeekSpecialistProvider(gateway)
        specialists = {
            "knowledge_agent": DeterministicMockSpecialist("knowledge_agent"),
            "community_agent": DeterministicMockSpecialist("community_agent"),
            "service_agent": deepseek,
            "governance_agent": deepseek,
            "modelops_agent": deepseek,
        }
        return BoundedGraphRuntime(
            router=RouterService(
                confidence_threshold=self.settings.local_router_confidence,
                deepseek_router=DeepSeekRouterAdapter(gateway),
                local_timeout_ms=self.settings.local_router_timeout_ms,
                deepseek_timeout_ms=self.settings.agent_run_timeout_seconds * 1000,
            ),
            planner=SupervisorPlanner(
                registry=agents,
                max_steps=self.settings.agent_max_steps,
                max_specialists=self.settings.agent_max_specialists,
            ),
            specialists=specialists,
            trace=TraceService(TraceRepository(session)),
            events=PersistentRuntimeEventSink(RuntimeEventRepository(session)),
            tool_executor=executor,
            approval_service=approvals,
            agent_allowlists={item.definition.code: item.version.tool_allowlist for item in agents.list_active()},
            safety=M4AgentSafetyAdapter(moderation),
            checkpoints=DatabaseRuntimeCheckpointStore.from_settings(
                RuntimeCheckpointRepository(session), self.settings
            ),
        )

    def command_processor(self, session: AsyncSession):
        return _LazyCompositionCommandProcessor(self, session)


class _LazyCompositionCommandProcessor:
    def __init__(self, factory: RuntimeCompositionFactory, session: AsyncSession) -> None:
        self._factory = factory
        self._session = session
        self._processor = None

    async def process(self, command) -> None:
        if self._processor is None:
            runtime = await self._factory.build_graph_runtime(self._session)
            self._processor = GraphRuntimeCommandProcessor(
                runtime, RuntimeStartContextLoader(self._session)
            )
        await self._processor.process(command)
