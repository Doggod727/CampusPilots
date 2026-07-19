from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Iterable
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.ai_knowledge.models import (
    Document,
    KnowledgeBase,
    KnowledgeBaseMember,
    RetrievalRun,
)
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService


class KnowledgeBaseNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在",
        )


class KnowledgeBaseConflict(AppError):
    def __init__(self, code: str = "RESOURCE_VERSION_CONFLICT") -> None:
        messages = {
            "RESOURCE_VERSION_CONFLICT": "知识库版本冲突",
            "KNOWLEDGE_BASE_IN_USE": "知识库仍被文档或会话引用",
            "DUPLICATE_RESOURCE": "知识库名称已存在",
        }
        super().__init__(
            status_code=409,
            code=code,
            message=messages.get(code, "知识库状态冲突"),
        )


class KnowledgeBaseConfigurationInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            code="VALIDATION_ERROR",
            message="知识库切分参数无效",
        )


class KnowledgeBaseVisibilityForbidden(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="KNOWLEDGE_BASE_VISIBILITY_FORBIDDEN",
            message="当前用户只能管理私人知识库",
        )


def require_allowed_visibility(user: AuthenticatedUser, visibility: str) -> None:
    if visibility != "private" and "knowledge:write_all" not in user.permissions:
        raise KnowledgeBaseVisibilityForbidden()


@dataclass(frozen=True)
class KnowledgeBaseMemberData:
    user_id: UUID
    access_level: str
    granted_at: datetime


@dataclass(frozen=True)
class KnowledgeBaseData:
    id: UUID
    name: str
    description: str | None
    visibility: str
    owner_user_id: UUID | None
    owner_department: str | None
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    collection_name: str
    document_count: int
    members: tuple[KnowledgeBaseMemberData, ...]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class KnowledgeBasePageData:
    items: tuple[KnowledgeBaseData, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class KnowledgeBaseMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


def knowledge_base_payload(item: KnowledgeBaseData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "name": item.name,
        "description": item.description,
        "visibility": item.visibility,
        "owner_user_id": str(item.owner_user_id) if item.owner_user_id else None,
        "owner_department": item.owner_department,
        "embedding_model": item.embedding_model,
        "chunk_size": item.chunk_size,
        "chunk_overlap": item.chunk_overlap,
        "collection_name": item.collection_name,
        "document_count": item.document_count,
        "members": [
            {
                "user_id": str(member.user_id),
                "access_level": member.access_level,
                "granted_at": member.granted_at.isoformat(),
            }
            for member in item.members
        ],
        "created_by": str(item.created_by),
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "version": item.version,
    }


def _literal_contains(column: object, value: str):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(column).like(f"%{escaped.lower()}%", escape="\\")


class KnowledgeRepository:
    """Knowledge-base persistence using a caller-owned async session."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def _scope_predicate(
        user_id: UUID,
        department: str | None,
        *,
        global_access: bool,
        write: bool,
    ):
        if global_access:
            return None
        member_access = ("editor", "owner") if write else ("viewer", "editor", "owner")
        member = exists(
            select(KnowledgeBaseMember.knowledge_base_id).where(
                KnowledgeBaseMember.knowledge_base_id == KnowledgeBase.id,
                KnowledgeBaseMember.user_id == user_id,
                KnowledgeBaseMember.access_level.in_(member_access),
            )
        )
        allowed = [KnowledgeBase.owner_user_id == user_id, member]
        if not write:
            allowed.append(KnowledgeBase.visibility == "public")
            if department:
                allowed.append(
                    (KnowledgeBase.visibility == "department")
                    & (KnowledgeBase.owner_department == department)
                )
        return or_(*allowed)

    async def list_allowed(
        self,
        user_id: UUID,
        department: str | None,
        global_read: bool,
        page: int,
        page_size: int,
        *,
        query: str | None = None,
        visibility: str | None = None,
    ) -> tuple[list[KnowledgeBase], int]:
        predicates = [KnowledgeBase.deleted_at.is_(None)]
        scope = self._scope_predicate(
            user_id, department, global_access=global_read, write=False
        )
        if scope is not None:
            predicates.append(scope)
        normalized = query.strip() if query else ""
        if normalized:
            predicates.append(
                or_(
                    _literal_contains(KnowledgeBase.name, normalized),
                    _literal_contains(KnowledgeBase.description, normalized),
                )
            )
        if visibility is not None:
            predicates.append(KnowledgeBase.visibility == visibility)

        count_statement = select(func.count(KnowledgeBase.id)).where(*predicates)
        total = int((await self.s.execute(count_statement)).scalar_one())
        statement = (
            select(KnowledgeBase)
            .where(*predicates)
            .order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.s.execute(statement)).scalars().all()
        return list(rows), total

    async def get_allowed(
        self,
        knowledge_base_id: UUID,
        user_id: UUID,
        department: str | None,
        *,
        global_access: bool,
        write: bool = False,
        for_update: bool = False,
    ) -> KnowledgeBase | None:
        predicates = [
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.deleted_at.is_(None),
        ]
        scope = self._scope_predicate(
            user_id, department, global_access=global_access, write=write
        )
        if scope is not None:
            predicates.append(scope)
        statement = select(KnowledgeBase).where(*predicates)
        if for_update:
            statement = statement.with_for_update()
        return (await self.s.execute(statement)).scalar_one_or_none()

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return (
            await self.s.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def access(self, knowledge_base_id: UUID, user_id: UUID) -> str | None:
        return (
            await self.s.execute(
                select(KnowledgeBaseMember.access_level).where(
                    KnowledgeBaseMember.knowledge_base_id == knowledge_base_id,
                    KnowledgeBaseMember.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def name_exists(
        self, name: str, *, excluding: UUID | None = None
    ) -> bool:
        predicates = [
            func.lower(KnowledgeBase.name) == name.lower(),
            KnowledgeBase.deleted_at.is_(None),
        ]
        if excluding is not None:
            predicates.append(KnowledgeBase.id != excluding)
        return bool(
            (
                await self.s.execute(
                    select(exists(select(KnowledgeBase.id).where(*predicates)))
                )
            ).scalar_one()
        )

    async def hydrate(
        self, knowledge_bases: Iterable[KnowledgeBase]
    ) -> list[KnowledgeBaseData]:
        items = list(knowledge_bases)
        if not items:
            return []
        ids = [item.id for item in items]
        member_rows = (
            await self.s.execute(
                select(KnowledgeBaseMember)
                .where(KnowledgeBaseMember.knowledge_base_id.in_(ids))
                .order_by(
                    KnowledgeBaseMember.knowledge_base_id,
                    KnowledgeBaseMember.granted_at,
                    KnowledgeBaseMember.user_id,
                )
            )
        ).scalars().all()
        document_rows = (
            await self.s.execute(
                select(Document.knowledge_base_id, func.count(Document.id))
                .where(
                    Document.knowledge_base_id.in_(ids),
                    Document.deleted_at.is_(None),
                    Document.status != "deleted",
                )
                .group_by(Document.knowledge_base_id)
            )
        ).all()
        members: dict[UUID, list[KnowledgeBaseMemberData]] = {item_id: [] for item_id in ids}
        for member in member_rows:
            members[member.knowledge_base_id].append(
                KnowledgeBaseMemberData(
                    user_id=member.user_id,
                    access_level=member.access_level,
                    granted_at=member.granted_at,
                )
            )
        counts = {knowledge_base_id: int(count) for knowledge_base_id, count in document_rows}
        return [
            KnowledgeBaseData(
                id=item.id,
                name=item.name,
                description=item.description,
                visibility=item.visibility,
                owner_user_id=item.owner_user_id,
                owner_department=item.owner_department,
                embedding_model=item.embedding_model,
                chunk_size=item.chunk_size,
                chunk_overlap=item.chunk_overlap,
                collection_name=item.collection_name,
                document_count=counts.get(item.id, 0),
                members=tuple(members[item.id]),
                created_by=item.created_by,
                created_at=item.created_at,
                updated_at=item.updated_at,
                version=item.version,
            )
            for item in items
        ]

    def add(self, knowledge_base: KnowledgeBase) -> None:
        self.s.add(knowledge_base)

    async def replace_members(
        self,
        knowledge_base_id: UUID,
        member_user_ids: Iterable[UUID],
        *,
        owner_user_id: UUID | None,
        granted_by: UUID,
        granted_at: datetime,
    ) -> None:
        desired = set(member_user_ids)
        if owner_user_id is not None:
            desired.discard(owner_user_id)
        existing = list(
            (
                await self.s.execute(
                    select(KnowledgeBaseMember)
                    .where(KnowledgeBaseMember.knowledge_base_id == knowledge_base_id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        existing_ids = {member.user_id for member in existing}
        remove_ids = existing_ids - desired
        if remove_ids:
            await self.s.execute(
                delete(KnowledgeBaseMember).where(
                    KnowledgeBaseMember.knowledge_base_id == knowledge_base_id,
                    KnowledgeBaseMember.user_id.in_(remove_ids),
                )
            )
        for user_id in sorted(desired - existing_ids, key=str):
            self.s.add(
                KnowledgeBaseMember(
                    knowledge_base_id=knowledge_base_id,
                    user_id=user_id,
                    access_level="viewer",
                    granted_by=granted_by,
                    granted_at=granted_at,
                )
            )

    async def has_dependents(self, knowledge_base_id: UUID) -> bool:
        documents = await self.s.execute(
            select(
                exists(
                    select(Document.id).where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.deleted_at.is_(None),
                        Document.status != "deleted",
                    )
                )
            )
        )
        if documents.scalar_one():
            return True
        retrievals = await self.s.execute(
            select(
                exists(
                    select(RetrievalRun.id).where(
                        RetrievalRun.knowledge_base_ids.contains([str(knowledge_base_id)])
                    )
                )
            )
        )
        return bool(retrievals.scalar_one())

    async def has_documents(self, knowledge_base_id: UUID) -> bool:
        return bool(
            (
                await self.s.execute(
                    select(func.count(Document.id)).where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.deleted_at.is_(None),
                        Document.status != "deleted",
                    )
                )
            ).scalar_one()
        )


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        repo: KnowledgeRepository,
        idempotency: IdempotencyService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.s = session
        self.r = repo
        self._idempotency = idempotency
        self._now = now or (lambda: datetime.now(UTC))

    async def list(
        self,
        *,
        user: AuthenticatedUser,
        page: int,
        page_size: int,
        query: str | None,
        visibility: str | None,
    ) -> KnowledgeBasePageData:
        rows, total = await self.r.list_allowed(
            user.user_id,
            user.department,
            "knowledge:read_all" in user.permissions,
            page,
            page_size,
            query=query,
            visibility=visibility,
        )
        return KnowledgeBasePageData(
            items=tuple(await self.r.hydrate(rows)),
            page=page,
            page_size=page_size,
            total=total,
        )

    async def require(
        self, knowledge_base_id: UUID, user: AuthenticatedUser, write: bool = False
    ) -> KnowledgeBase:
        item = await self.r.get_allowed(
            knowledge_base_id,
            user.user_id,
            user.department,
            global_access=(
                "knowledge:write_all" if write else "knowledge:read_all"
            )
            in user.permissions,
            write=write,
        )
        if item is None:
            raise KnowledgeBaseNotFound()
        return item

    async def get_data(
        self, knowledge_base_id: UUID, user: AuthenticatedUser
    ) -> KnowledgeBaseData:
        item = await self.require(knowledge_base_id, user)
        return (await self.r.hydrate([item]))[0]

    async def create(
        self,
        user: AuthenticatedUser,
        *,
        idempotency_key: str,
        request_id: str,
        request_body: object,
        member_user_ids: Iterable[UUID] = (),
        **values: object,
    ) -> KnowledgeBaseMutationResult:
        idempotency = self._require_idempotency()
        decision = await idempotency.begin(
            user_id=user.user_id,
            endpoint="POST /api/v1/knowledge-bases",
            idempotency_key=idempotency_key,
            request_body=request_body,
        )
        if decision.replay is not None:
            return KnowledgeBaseMutationResult(
                decision.replay.response_status,
                str(decision.replay.response_body["request_id"]),
                dict(decision.replay.response_body),
            )
        if decision.pending:
            raise IdempotencyConflict()
        name = str(values["name"])
        if await self.r.name_exists(name):
            raise KnowledgeBaseConflict("DUPLICATE_RESOURCE")
        now = self._time()
        knowledge_base_id = uuid4()
        visibility = str(values.get("visibility", "private"))
        require_allowed_visibility(user, visibility)
        owner_department = values.get("owner_department")
        if visibility == "department" and owner_department is None:
            owner_department = user.department
        item = KnowledgeBase(
            id=knowledge_base_id,
            name=name,
            description=values.get("description"),
            visibility=visibility,
            owner_user_id=user.user_id,
            owner_department=owner_department,
            embedding_model=str(values.get("embedding_model", "bge-small-zh-v1.5")),
            chunk_size=int(values.get("chunk_size", 500)),
            chunk_overlap=int(values.get("chunk_overlap", 80)),
            collection_name=f"kb_{knowledge_base_id.hex}",
            version=1,
            created_by=user.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._validate_chunks(item.chunk_size, item.chunk_overlap)
        self.r.add(item)
        try:
            async with self.s.begin_nested():
                await self.s.flush()
        except IntegrityError as exc:
            raise KnowledgeBaseConflict("DUPLICATE_RESOURCE") from exc
        await self.r.replace_members(
            item.id,
            member_user_ids,
            owner_user_id=item.owner_user_id,
            granted_by=user.user_id,
            granted_at=now,
        )
        await self.s.flush()
        data = (await self.r.hydrate([item]))[0]
        body = self._body(data, request_id, now)
        if not await idempotency.complete(
            record_id=decision.record_id,
            response_status=201,
            response_body=body,
            resource_type="knowledge_base",
            resource_id=str(item.id),
        ):
            raise IdempotencyConflict()
        return KnowledgeBaseMutationResult(201, request_id, body)

    async def update(
        self,
        knowledge_base_id: UUID,
        user: AuthenticatedUser,
        version: int,
        values: dict[str, object],
        *,
        member_user_ids: Iterable[UUID] | None = None,
        idempotency_key: str | None = None,
        request_id: str,
        request_body: object,
    ) -> KnowledgeBaseMutationResult:
        item = await self.r.get_allowed(
            knowledge_base_id,
            user.user_id,
            user.department,
            global_access="knowledge:write_all" in user.permissions,
            write=True,
            for_update=True,
        )
        if item is None:
            raise KnowledgeBaseNotFound()
        decision = None
        if idempotency_key is not None:
            idempotency = self._require_idempotency()
            decision = await idempotency.begin(
                user_id=user.user_id,
                endpoint=f"PATCH /api/v1/knowledge-bases/{knowledge_base_id}",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if decision.replay is not None:
                return KnowledgeBaseMutationResult(
                    decision.replay.response_status,
                    str(decision.replay.response_body["request_id"]),
                    dict(decision.replay.response_body),
                )
            if decision.pending:
                raise IdempotencyConflict()
        if item.version != version:
            raise KnowledgeBaseConflict()
        if "name" in values:
            name = str(values["name"])
            if await self.r.name_exists(name, excluding=item.id):
                raise KnowledgeBaseConflict("DUPLICATE_RESOURCE")
        chunk_size = int(values.get("chunk_size", item.chunk_size))
        chunk_overlap = int(values.get("chunk_overlap", item.chunk_overlap))
        self._validate_chunks(chunk_size, chunk_overlap)
        require_allowed_visibility(user, str(values.get("visibility", item.visibility)))
        for key in (
            "name",
            "description",
            "visibility",
            "owner_department",
            "chunk_size",
            "chunk_overlap",
        ):
            if key in values:
                setattr(item, key, values[key])
        now = self._time()
        item.version += 1
        item.updated_at = now
        try:
            async with self.s.begin_nested():
                await self.s.flush()
        except IntegrityError as exc:
            raise KnowledgeBaseConflict("DUPLICATE_RESOURCE") from exc
        if member_user_ids is not None:
            await self.r.replace_members(
                item.id,
                member_user_ids,
                owner_user_id=item.owner_user_id,
                granted_by=user.user_id,
                granted_at=now,
            )
            await self.s.flush()
        data = (await self.r.hydrate([item]))[0]
        body = self._body(data, request_id, now)
        if decision is not None:
            if not await self._require_idempotency().complete(
                record_id=decision.record_id,
                response_status=200,
                response_body=body,
                resource_type="knowledge_base",
                resource_id=str(item.id),
            ):
                raise IdempotencyConflict()
        return KnowledgeBaseMutationResult(200, request_id, body)

    async def delete(
        self,
        knowledge_base_id: UUID,
        user: AuthenticatedUser,
        version: int | None = None,
    ) -> None:
        item = await self.r.get_allowed(
            knowledge_base_id,
            user.user_id,
            user.department,
            global_access="knowledge:write_all" in user.permissions,
            write=True,
            for_update=True,
        )
        if item is None:
            raise KnowledgeBaseNotFound()
        if version is not None and item.version != version:
            raise KnowledgeBaseConflict()
        if await self.r.has_dependents(knowledge_base_id):
            raise KnowledgeBaseConflict("KNOWLEDGE_BASE_IN_USE")
        item.deleted_at = self._time()
        item.updated_at = item.deleted_at
        item.version += 1
        await self.s.flush()

    def _require_idempotency(self) -> IdempotencyService:
        if self._idempotency is None:
            raise RuntimeError("Knowledge-base mutation requires IdempotencyService")
        return self._idempotency

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _validate_chunks(chunk_size: int, chunk_overlap: int) -> None:
        if chunk_overlap >= chunk_size:
            raise KnowledgeBaseConfigurationInvalid()

    @staticmethod
    def _body(
        item: KnowledgeBaseData, request_id: str, timestamp: datetime
    ) -> dict[str, object]:
        return {
            "code": "OK",
            "message": "success",
            "data": knowledge_base_payload(item),
            "request_id": request_id,
            "timestamp": timestamp.isoformat(),
        }
