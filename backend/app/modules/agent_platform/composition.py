from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.agent_platform.approvals import ApprovalRepository, ApprovalService, DatabaseApprovalVerifier
from app.modules.agent_platform.catalog_persistence import CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.checkpointing import DatabaseRuntimeCheckpointStore, PersistentRuntimeEventSink, RuntimeStartPayloadCodec
from app.modules.agent_platform.deepseek import DeepSeekGateway, DeepSeekRouterAdapter, DeepSeekSpecialistProvider
from app.modules.agent_platform.internal_auth import InternalUserContextLoader
from app.modules.agent_platform.models import AgentRun
from app.modules.agent_platform.orchestration.runtime import BoundedGraphRuntime
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
from app.modules.agent_platform.tool_gateway.community_adapters import (
    CommunityPostPublishToolHandler, CommunityTopicSummaryToolHandler,
    EventCreateToolHandler, EventRegisterToolHandler, EventSearchToolHandler,
    LostFoundMatchesToolHandler, LostFoundPublishToolHandler,
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
from app.modules.community.encryption import CommunityCipher, CommunityEncryptionUnavailable
from app.modules.community.events import EventQueryService, EventService
from app.modules.community.lost_found import LostFoundQueryService, LostFoundService
from app.modules.community.matcher import LostFoundMatcherService
from app.modules.community.profiles import PlatformPublicUserProfileAdapter
from app.modules.community.posts import PostQueryService, PostService
from app.modules.community.registrations import EventRegistrationService
from app.modules.community.repositories import (
    EventRepository, LostFoundRepository, PostRepository, TopicRepository,
)
from app.modules.community.topics import TopicService
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.audit import AuditService, redact
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository, IdempotencyRecordRepository, ModerationCaseRepository,
    RbacRepository, SensitiveWordRepository, UserRepository,
)
from app.modules.ai_knowledge.knowledge import KnowledgeRepository, KnowledgeService
from app.modules.ai_knowledge.retrieval import RetrievalService
from app.modules.ai_knowledge.tool_adapters import (
    KnowledgeAnswerToolHandler,
    KnowledgeSearchToolHandler,
)
from app.modules.ai_knowledge.vectors import (
    BgeSmallZhEmbeddingProvider,
    ChromaVectorStore,
    LazyChromaClient,
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

    async def build_tool_executor(self, session: AsyncSession, catalogs=None, gateway=None):
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
        profiles = PlatformPublicUserProfileAdapter(session)
        event_repository = EventRepository(session)
        event_queries = EventQueryService(event_repository, profiles)
        community_idempotency = IdempotencyService(
            session=session, repository=IdempotencyRecordRepository(session)
        )
        events = EventService(
            session=session, repository=event_repository, queries=event_queries,
            moderation=moderation, idempotency=community_idempotency, audit=audit,
        )
        event_registrations = EventRegistrationService(
            session=session, repository=event_repository, profiles=profiles,
            idempotency=IdempotencyService(session=session,
                repository=IdempotencyRecordRepository(session)), audit=audit,
        )
        topic_repository = TopicRepository(session)
        topics = TopicService(
            session=session, repository=topic_repository,
            idempotency=community_idempotency, audit=audit,
        )
        post_repository = PostRepository(session)
        post_queries = PostQueryService(post_repository, profiles)
        posts = PostService(
            session=session, repository=post_repository, queries=post_queries,
            moderation=moderation, idempotency=community_idempotency, audit=audit,
        )
        lost_repository = LostFoundRepository(session)
        lost_queries = LostFoundQueryService(lost_repository, profiles)
        matcher = LostFoundMatcherService(session=session, repository=lost_repository,
            queries=lost_queries,
            algorithm_version=self.settings.community_match_algorithm_version)
        try:
            community_cipher = CommunityCipher(self.settings.community_data_encryption_key)
        except CommunityEncryptionUnavailable:
            community_cipher = _UnavailableCommunityCipher()
        lost_found = LostFoundService(session=session, repository=lost_repository,
            queries=lost_queries, cipher=community_cipher,  # type: ignore[arg-type]
            moderation=moderation,
            idempotency=IdempotencyService(session=session,
                repository=IdempotencyRecordRepository(session)), audit=audit, matcher=matcher)
        knowledge = KnowledgeService(session, KnowledgeRepository(session))
        retrieval = RetrievalService(
            session,
            knowledge,
            BgeSmallZhEmbeddingProvider(str(self.settings.knowledge_embedding_model_path)),
            ChromaVectorStore(LazyChromaClient(str(self.settings.knowledge_chroma_path))),
            self.settings.knowledge_score_threshold,
        )
        knowledge_gateway = gateway or self._deepseek_gateway()
        handlers = build_mock_handlers()
        handlers.update({
            "knowledge.search": KnowledgeSearchToolHandler(retrieval),
            "knowledge.answer": KnowledgeAnswerToolHandler(retrieval, knowledge_gateway),
            "service.get_guide": ServiceGuideToolHandler(guides),
            "work_order.create": WorkOrderCreateToolHandler(work_orders),
            "work_order.get": WorkOrderGetToolHandler(work_orders),
            "electricity.get_balance": ElectricityBalanceToolHandler(electricity),
            "electricity.create_topup_request": ElectricityTopupToolHandler(electricity),
            "event.search": EventSearchToolHandler(event_queries),
            "event.register": EventRegisterToolHandler(event_registrations),
            "event.create": EventCreateToolHandler(events),
            "community.post.publish": CommunityPostPublishToolHandler(posts, topics),
            "community.topic.summarize": CommunityTopicSummaryToolHandler(post_queries),
            "lost_found.publish": LostFoundPublishToolHandler(lost_found),
            "lost_found.search_matches": LostFoundMatchesToolHandler(matcher),
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
        gateway = self._deepseek_gateway()
        executor, approvals, moderation = await self.build_tool_executor(
            session, (agents, tools), gateway=gateway
        )
        specialists = {
            agent_code: DeepSeekSpecialistProvider(
                gateway, tools=self._tool_descriptors(tools, agents, agent_code)
            )
            for agent_code in (
                "knowledge_agent",
                "community_agent",
                "service_agent",
                "governance_agent",
                "modelops_agent",
            )
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

    def _deepseek_gateway(self) -> DeepSeekGateway:
        return DeepSeekGateway(
            api_key=self.settings.deepseek_api_key.get_secret_value(),
            base_url=str(self.settings.deepseek_base_url),
            model=self.settings.deepseek_model,
            timeout_seconds=self.settings.agent_run_timeout_seconds,
        )

    @staticmethod
    def _tool_descriptors(tools, agents, agent_code: str) -> tuple[dict[str, Any], ...]:
        """Return name/version/description/schema descriptors for an agent's allowlist."""

        try:
            registration = agents.get_active(agent_code)
        except Exception:
            return ()
        descriptors = []
        for tool_name in registration.version.tool_allowlist:
            try:
                contract = tools.resolve(tool_name)
            except Exception:
                continue
            definition = contract.definition
            descriptors.append(
                {
                    "name": definition.name,
                    "version": definition.version,
                    "description": definition.description,
                    "input_schema": definition.input_schema,
                }
            )
        return tuple(descriptors)

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
                runtime,
                RuntimeStartContextLoader(self._session),
                start_codec=RuntimeStartPayloadCodec(
                    self._factory.settings.agent_checkpoint_secret.get_secret_value()
                ) if self._factory.settings.agent_checkpoint_secret is not None else None,
            )
        await self._processor.process(command)


class _UnavailableCommunityCipher:
    def encrypt(self, _plaintext: str) -> bytes:
        raise CommunityEncryptionUnavailable()

    def decrypt(self, _ciphertext: bytes) -> str:
        raise CommunityEncryptionUnavailable()
