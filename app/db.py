from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings
class Base(DeclarativeBase): pass
SessionLocal = sessionmaker(expire_on_commit=False)

def configure_engine():
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal.configure(bind=engine)
    return engine

def get_db():
    if SessionLocal.kw.get('bind') is None:
        configure_engine()
    with SessionLocal() as db:
        yield db
