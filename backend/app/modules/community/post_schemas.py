from datetime import datetime
from enum import Enum
from math import ceil
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.community.posts import PostData, PostPageData
from app.modules.community.topic_schemas import PageMetaData, TopicDataModel
from app.shared.responses import SuccessResponse


class CommunityContentStatus(str, Enum):
    pending_review = "pending_review"
    published = "published"
    rejected = "rejected"
    hidden = "hidden"
    deleted = "deleted"


class PostSort(str, Enum):
    newest = "-published_at"
    oldest = "published_at"


class PublicAuthorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None
    display_name: str
    avatar_url: str | None
    is_anonymous: bool


class PostInteractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liked: bool
    favorited: bool


class PostDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    topic: TopicDataModel
    author: PublicAuthorModel
    title: str = Field(max_length=120)
    content_markdown: str = Field(max_length=5000)
    is_anonymous: bool
    status: CommunityContentStatus
    moderation_case_id: UUID | None = None
    like_count: int = Field(ge=0)
    favorite_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    report_count: int = Field(ge=0)
    interaction: PostInteractionModel
    published_at: datetime | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PostCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: UUID
    title: str = Field(min_length=2, max_length=120)
    content_markdown: str = Field(min_length=1, max_length=5000)
    is_anonymous: bool = False


class PostUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: UUID | None = None
    title: str | None = Field(default=None, min_length=2, max_length=120)
    content_markdown: str | None = Field(default=None, min_length=1, max_length=5000)
    is_anonymous: bool | None = None
    version: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_null_changes(cls, value: object) -> object:
        if isinstance(value, dict):
            for key in ("topic_id", "title", "content_markdown", "is_anonymous"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} cannot be null")
        return value


class PostPageResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PostDataModel]
    pagination: PageMetaData


PostResponse = SuccessResponse[PostDataModel]
PostPageResponse = SuccessResponse[PostPageResponseData]


def post_model(item: PostData) -> PostDataModel:
    return PostDataModel.model_validate(item, from_attributes=True)


def post_page_model(page: PostPageData) -> PostPageResponseData:
    return PostPageResponseData(
        items=[post_model(item) for item in page.items],
        pagination=PageMetaData(
            page=page.page,
            page_size=page.page_size,
            total=page.total,
            total_pages=ceil(page.total / page.page_size) if page.total else 0,
        ),
    )
