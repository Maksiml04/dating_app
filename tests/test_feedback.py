from uuid import uuid4
from app.dialogue import handle_feedback

class FakeDB:
    def __init__(self,shown=False):
        self.shown=shown
        self.items=[]
        self.calls=0
    def scalar(self,query):
        self.calls+=1
        return None if self.calls==1 else (uuid4() if self.shown else None)
    def add(self,item): self.items.append(item)

def update(data):
    return {'callback_query':{'id':'abc','data':data,'message':{'chat':{'id':123}}}}

def test_feedback_requires_impression():
    db=FakeDB(False)
    assert handle_feedback(db,update(f'fb:like:{uuid4()}')) is False
    assert db.items==[]

def test_feedback_accepted_for_shown_card():
    db=FakeDB(True)
    assert handle_feedback(db,update(f'fb:dislike:{uuid4()}')) is True
    assert len(db.items)==1
    assert db.items[0].reaction=='dislike'

def test_malformed_callback_rejected():
    assert handle_feedback(FakeDB(True),update('fb:like:bad')) is False
