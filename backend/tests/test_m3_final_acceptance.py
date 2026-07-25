import inspect
from pathlib import Path

import yaml

from app.main import create_app
from app.modules.agent_platform import composition

M3_OPERATIONS = {
    "listTopics", "createTopic", "getTopic", "updateTopic", "deleteTopic",
    "listPosts", "createPost", "getPost", "updatePost", "deletePost",
    "listPostComments", "createComment", "updateComment", "deleteComment",
    "putPostReaction", "deletePostReaction", "createContentReport",
    "revealAnonymousIdentity", "listCampusEvents", "createCampusEvent",
    "getCampusEvent", "updateCampusEvent", "cancelCampusEvent",
    "listEventRegistrations", "registerCampusEvent", "cancelMyEventRegistration",
    "listLostFoundItems", "createLostFoundItem", "getLostFoundItem",
    "updateLostFoundItem", "deleteLostFoundItem", "listLostFoundMatches",
    "createLostFoundClaim", "listMyLostFoundClaims", "getLostFoundClaim",
    "decideLostFoundClaim", "getLostFoundClaimContact",
    "confirmLostFoundClaimCompletion",
}


def openapi_operations() -> dict[str, dict[str, object]]:
    path = Path(__file__).parents[2] / "docx" / "deliverables" / "openapi.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {operation["operationId"]: operation for item in document["paths"].values()
            for method, operation in item.items()
            if method in {"get", "post", "put", "patch", "delete"}}


def test_all_38_m3_operations_are_unique_registered_and_authenticated() -> None:
    operations = openapi_operations()
    assert len(M3_OPERATIONS) == 38 and M3_OPERATIONS <= operations.keys()
    assert len(operations) == len(set(operations))
    ids = [route.operation_id for route in create_app().routes
           if getattr(route, "operation_id", None) in M3_OPERATIONS]
    assert len(ids) == 38 and set(ids) == M3_OPERATIONS
    assert all("401" in operations[value]["responses"] for value in M3_OPERATIONS)


def test_four_m3_tools_are_real_in_the_only_runtime_composition() -> None:
    source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    markers = {"event.search": "EventSearchToolHandler",
               "event.register": "EventRegisterToolHandler",
               "lost_found.publish": "LostFoundPublishToolHandler",
               "lost_found.search_matches": "LostFoundMatchesToolHandler"}
    assert all(f'"{name}": {handler}' in source for name, handler in markers.items())
