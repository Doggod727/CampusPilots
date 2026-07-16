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
