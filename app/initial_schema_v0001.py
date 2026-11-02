"""Зафиксированная схема первой миграции (снимок моделей v0.1).

Изменять эту схему при развитии app.models нельзя: миграции должны быть
воспроизводимы на пустой базе и после следующих обновлений приложения.
"""
"""Доменная модель. Публикации неизменяемы после выпуска на уровне приложения."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, BigInteger, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase):
    pass

def new_id(): return uuid.uuid4()
def utcnow(): return datetime.now(timezone.utc)

class Canonical(Base):
    __tablename__ = 'canonical'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("kind IN ('place','event')", name='canonical_kind'),)

class CanonicalAlias(Base):
    __tablename__ = 'canonical_alias'
    alias_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('canonical.id'), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('canonical.id'), nullable=False)
    __table_args__ = (CheckConstraint('alias_id <> target_id', name='alias_not_self'),)

class PublicationVersion(Base):
    __tablename__ = 'publication_version'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('canonical.id'), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default='', nullable=False)
    category: Mapped[str] = mapped_column(String(100), default='другое', nullable=False)
    district: Mapped[str | None] = mapped_column(String(120))
    metro: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text)
    indoors: Mapped[bool | None] = mapped_column(Boolean)
    source_url: Mapped[str | None] = mapped_column(Text)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_verification_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    index_status: Mapped[str] = mapped_column(String(20), default='pending', nullable=False)
    canonical: Mapped[Canonical] = relationship()
    __table_args__ = (UniqueConstraint('canonical_id','version'), Index('ix_publication_title', 'title'),)

class Occurrence(Base):
    __tablename__ = 'occurrence'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    publication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('publication_version.id'), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='available', nullable=False)
    __table_args__ = (CheckConstraint('ends_at > starts_at', name='occ_time_order'),)

class Offer(Base):
    __tablename__ = 'offer'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    publication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('publication_version.id'), nullable=False, index=True)
    occurrence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('occurrence.id'))
    price_kopeks: Mapped[int | None] = mapped_column(Integer)
    price_scope: Mapped[str] = mapped_column(String(20), default='pair', nullable=False)
    price_is_bounded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (CheckConstraint('price_kopeks IS NULL OR price_kopeks >= 0', name='nonnegative_price'), CheckConstraint("price_scope IN ('pair','person')", name='offer_scope'),)

class DialogueSession(Base):
    __tablename__ = 'dialogue_session'
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    generation_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default='idle', nullable=False)
    constraints: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class RequestOperation(Base):
    __tablename__ = 'request_operation'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('dialogue_session.chat_id'), nullable=False)
    generation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='queued', nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('chat_id','generation_id'),)

class Inbox(Base):
    __tablename__ = 'telegram_inbox'
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='pending', nullable=False)

class Outbox(Base):
    __tablename__ = 'telegram_outbox'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation_id: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    publication_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='pending', nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index('ix_outbox_pending','status','next_attempt_at'),)

class Impression(Base):
    __tablename__ = 'impression'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('publication_version.id'), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Feedback(Base):
    __tablename__ = 'feedback'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('publication_version.id'), nullable=False)
    reaction: Mapped[str] = mapped_column(String(20), nullable=False)

class UserPreference(Base):
    __tablename__ = 'user_preference'
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AuditLog(Base):
    __tablename__ = 'audit_log'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
