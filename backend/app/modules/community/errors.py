from app.core.errors import AppError


class TopicNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="TOPIC_NOT_FOUND", message="社区话题不存在")


class TopicCodeConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOPIC_CODE_CONFLICT", message="社区话题代码已存在")


class TopicNameConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOPIC_NAME_CONFLICT", message="社区话题名称已存在")


class TopicHasPosts(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOPIC_HAS_ACTIVE_POSTS", message="存在未删除帖子，无法删除话题")


class CommunityResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="RESOURCE_VERSION_CONFLICT", message="资源版本冲突")


class PostNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="POST_NOT_FOUND", message="帖子不存在或不可见")


class CommunityAnonymousNotAllowed(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            code="COMMUNITY_ANONYMOUS_NOT_ALLOWED",
            message="该话题不允许匿名发布",
        )


class CommentNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="COMMENT_NOT_FOUND", message="评论不存在或不可见")


class CommentParentInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="COMMENT_PARENT_INVALID", message="父评论无效")


class CommunityContentPendingReview(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="COMMUNITY_CONTENT_PENDING_REVIEW",
                         message="内容正在审核，暂不可互动")


class ContentReportTargetNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="CONTENT_REPORT_TARGET_NOT_FOUND",
                         message="举报目标不存在或不可见")


class AnonymousIdentityNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="ANONYMOUS_IDENTITY_NOT_FOUND",
                         message="匿名身份不存在或不可反查")
