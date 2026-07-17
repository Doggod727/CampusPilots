from datetime import datetime
from math import ceil
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.ai_knowledge.knowledge import (
    KnowledgeBaseData,
    KnowledgeBasePageData,
)
from app.modules.platform.user_schemas import PageMetaData
from app.shared.responses import SuccessResponse

KnowledgeBaseVisibility = Literal["public", "department", "private"]
KnowledgeBaseAccessLevel = Literal["viewer", "editor", "owner"]


class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    visibility: KnowledgeBaseVisibility
    owner_department: str | None = Field(default=None, max_length=100)
    embedding_model: Literal["bge-small-zh-v1.5"] = "bge-small-zh-v1.5"
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=80, ge=0, le=500)
    member_user_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "KnowledgeBaseCreateRequest":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if len(self.member_user_ids) != len(set(self.member_user_ids)):
            raise ValueError("member_user_ids must contain unique values")
        return self


class KnowledgeBaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    visibility: KnowledgeBaseVisibility | None = None
    owner_department: str | None = Field(default=None, max_length=100)
    chunk_size: int | None = Field(default=None, ge=100, le=2000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=500)
    member_user_ids: list[UUID] | None = None
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "KnowledgeBaseUpdateRequest":
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("at least one property besides version is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "visibility" in self.model_fields_set and self.visibility is None:
            raise ValueError("visibility cannot be null")
        if "chunk_size" in self.model_fields_set and self.chunk_size is None:
            raise ValueError("chunk_size cannot be null")
        if "chunk_overlap" in self.model_fields_set and self.chunk_overlap is None:
            raise ValueError("chunk_overlap cannot be null")
        if self.chunk_size is not None and self.chunk_overlap is not None:
            if self.chunk_overlap >= self.chunk_size:
                raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.member_user_ids is not None:
            if len(self.member_user_ids) != len(set(self.member_user_ids)):
                raise ValueError("member_user_ids must contain unique values")
        return self


class KnowledgeBaseMemberModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    access_level: KnowledgeBaseAccessLevel
    granted_at: datetime


class KnowledgeBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None
    visibility: KnowledgeBaseVisibility
    owner_user_id: UUID | None
    owner_department: str | None
    embedding_model: Literal["bge-small-zh-v1.5"]
    chunk_size: int = Field(ge=100, le=2000)
    chunk_overlap: int = Field(ge=0, le=500)
    collection_name: str = Field(pattern=r"^kb_[a-f0-9]{32}$")
    document_count: int = Field(ge=0)
    members: list[KnowledgeBaseMemberModel]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class KnowledgeBasePageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeBaseModel]
    pagination: PageMetaData


KnowledgeBaseResponse = SuccessResponse[KnowledgeBaseModel]
KnowledgeBasePageResponse = SuccessResponse[KnowledgeBasePageModel]


def knowledge_base_model(item: KnowledgeBaseData) -> KnowledgeBaseModel:
    return KnowledgeBaseModel(
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
        document_count=item.document_count,
        members=[
            KnowledgeBaseMemberModel(
                user_id=member.user_id,
                access_level=member.access_level,
                granted_at=member.granted_at,
            )
            for member in item.members
        ],
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
    )


def knowledge_base_page_model(item: KnowledgeBasePageData) -> KnowledgeBasePageModel:
    return KnowledgeBasePageModel(
        items=[knowledge_base_model(value) for value in item.items],
        pagination=PageMetaData(
            page=item.page,
            page_size=item.page_size,
            total=item.total,
            total_pages=ceil(item.total / item.page_size) if item.total else 0,
        ),
    )
