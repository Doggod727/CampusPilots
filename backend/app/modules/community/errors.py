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


class EventNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="EVENT_NOT_FOUND", message="活动不存在或不可见")


class EventTimeInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="EVENT_TIME_INVALID", message="活动时间范围无效")


class EventStateInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVENT_STATE_INVALID", message="当前活动状态不允许此操作")


class EventCapacityInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVENT_CAPACITY_INVALID", message="活动容量不能低于已报名人数")


class EventRegistrationBusy(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVENT_REGISTRATION_BUSY", message="报名繁忙，请稍后重试")


class EventRegistrationClosed(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVENT_REGISTRATION_CLOSED", message="活动报名已关闭")


class EventCapacityFull(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="EVENT_CAPACITY_FULL", message="活动名额已满")


class EventRegistrationNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="EVENT_REGISTRATION_NOT_FOUND", message="报名记录不存在")


class LostFoundItemNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="LOST_FOUND_ITEM_NOT_FOUND", message="失物招领记录不存在或不可见")


class LostFoundStateInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="LOST_FOUND_STATE_INVALID", message="当前失物招领状态不允许此操作")


class LostFoundClaimInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="LOST_FOUND_CLAIM_INVALID", message="存在进行中的认领，无法执行此操作")


class CommunityMatchConfigInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=500, code="COMMUNITY_MATCH_CONFIG_INVALID", message="失物匹配配置无效")


class LostFoundClaimNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="LOST_FOUND_CLAIM_NOT_FOUND", message="认领记录不存在或不可见")


class LostFoundClaimConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="LOST_FOUND_CLAIM_CONFLICT", message="已存在进行中的认领")


class LostFoundClaimStateInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="LOST_FOUND_CLAIM_STATE_INVALID", message="当前认领状态不允许此操作")
