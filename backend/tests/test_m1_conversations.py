import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.ai_knowledge.conversations import ConversationService


class Session:
    def __init__(self): self.added=[]
    def add(self,x): self.added.append(x)
    def add_all(self,x): self.added.extend(x)
    async def execute(self,statement): return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[]))
class Repo:
    async def get_owned(self,*args,**kwargs): return SimpleNamespace(last_message_at=None)
    async def next_sequence(self,*args): return 1


def test_append_turn_creates_consecutive_user_and_pending_assistant():
    service=ConversationService(Session(),Repo())
    user,assistant=asyncio.run(service.append_turn(uuid4(),uuid4(),"问题","request-123"))
    assert (user.sequence_no,user.status)==(1,"completed")
    assert (assistant.sequence_no,assistant.status)==(2,"pending")


def test_terminal_message_cannot_be_completed_twice():
    message=SimpleNamespace(status="pending",content="",finish_reason=None,completed_at=None)
    ConversationService.complete(message,"answer")
    with pytest.raises(Exception) as exc: ConversationService.complete(message,"again")
    assert exc.value.code=="MESSAGE_STATE_CONFLICT"
