from datetime import datetime
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.community.comments import CommentData, CommentPageData
from app.modules.community.post_schemas import CommunityContentStatus, PublicAuthorModel
from app.modules.community.topic_schemas import PageMetaData
from app.shared.responses import SuccessResponse


class CommentDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    post_id: UUID
    parent_comment_id: UUID | None = None
    author: PublicAuthorModel
    content_markdown: str = Field(max_length=1000)
    is_anonymous: bool
    status: CommunityContentStatus
    moderation_case_id: UUID | None = None
    published_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_comment_id: UUID | None = None
    content_markdown: str = Field(min_length=1, max_length=1000)
    is_anonymous: bool = False


class CommentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_markdown: str = Field(min_length=1, max_length=1000)
    version: int = Field(ge=1)


class CommentPageResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CommentDataModel]
    pagination: PageMetaData


CommentResponse = SuccessResponse[CommentDataModel]
CommentPageResponse = SuccessResponse[CommentPageResponseData]


def comment_model(item: CommentData) -> CommentDataModel:
    return CommentDataModel.model_validate(item, from_attributes=True)


def comment_page_model(page: CommentPageData) -> CommentPageResponseData:
    return CommentPageResponseData(
        items=[comment_model(item) for item in page.items],
        pagination=PageMetaData(
            page=page.page, page_size=page.page_size, total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0,
        ),
    )
