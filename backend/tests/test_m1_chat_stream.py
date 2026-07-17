import json

from app.modules.ai_knowledge.chat_routes import sse


def test_sse_event_is_safe_and_deterministic():
    value=sse("delta",{"sequence":1,"content":"校园答案"})
    assert value.startswith("event: delta\ndata: ") and value.endswith("\n\n")
    assert json.loads(value.split("data: ",1)[1])=={"sequence":1,"content":"校园答案"}
