from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.draft_moderation import reject_draft

class FakeDb:
    def __init__(self): self.added=[]
    def add(self, obj): self.added.append(obj)

def draft(status='needs_review'):
    return SimpleNamespace(id=uuid4(), source_message_id=uuid4(), status=status)

def test_reject_pending_draft_audited():
    db=FakeDb(); item=draft()
    reject_draft(db,item,actor='api-admin',reason='Не подтверждена дата')
    assert item.status=='rejected'
    assert len(db.added)==1
    assert db.added[0].details['reason']=='Не подтверждена дата'

def test_cannot_reject_published():
    db=FakeDb(); item=draft('published')
    with pytest.raises(ValueError): reject_draft(db,item,actor='api-admin',reason='Не подходит')
    assert db.added==[]

def test_reject_requires_reason():
    db=FakeDb(); item=draft()
    with pytest.raises(ValueError): reject_draft(db,item,actor='api-admin',reason='  ')
    assert item.status=='needs_review'
