from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator

class SearchRequest(BaseModel):
    query: str = Field(default='', max_length=500)
    budget_total_kopeks: int | None = Field(default=None, ge=0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    district: str | None = None
    time_period: Literal['morning','day','evening','night'] | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=720)
    metro: str | None = None
    category: str | None = None
    interests: list[str] = Field(default_factory=list, max_length=8)
    format: str | None = None
    indoors: bool | None = None
    exclude_canonical_ids: list[UUID] = Field(default_factory=list)
    limit: int = Field(default=3, ge=1, le=20)
    @model_validator(mode='after')
    def validate_interval(self):
        if self.starts_at and (self.starts_at.tzinfo is None): raise ValueError('starts_at требует часовой пояс')
        if self.ends_at and (self.ends_at.tzinfo is None): raise ValueError('ends_at требует часовой пояс')
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at: raise ValueError('Неверный временной интервал')
        return self

class CandidateOut(BaseModel):
    canonical_id: UUID
    publication_version_id: UUID
    occurrence_id: UUID | None = None
    offer_id: UUID | None = None
    title: str
    description: str
    category: str
    address: str | None
    source_url: str | None
    price_total_kopeks: int | None
    price_is_bounded: bool | None = None
    valid_until: datetime
    reason_codes: list[str] = Field(default_factory=list)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    booking_url: str | None = None
    indoors: bool | None = None

class PublishIn(BaseModel):
    canonical_id: UUID | None = None
    kind: Literal['place','event']
    parent_place_id: UUID | None = None
    title: str = Field(min_length=2,max_length=300)
    description: str = ''
    category: str = 'другое'
    district: str | None = None
    metro: str | None = None
    address: str | None = None
    indoors: bool | None = None
    source_url: str | None = None
    booking_url: str | None = None
    opening_hours: dict | None = None
    calendar_exceptions: dict | None = None
    last_verified_at: datetime
    valid_until: datetime
    next_verification_at: datetime | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    price_kopeks: int | None = Field(default=None, ge=0)
    price_scope: Literal['pair','person'] = 'pair'
    price_is_bounded: bool = False
    @model_validator(mode='after')
    def check_times(self):
        for field in ('last_verified_at','valid_until','starts_at','ends_at','next_verification_at'):
            d = getattr(self,field)
            if d is not None and d.tzinfo is None: raise ValueError(f'{field} должен содержать часовой пояс')
        if self.valid_until <= self.last_verified_at: raise ValueError('valid_until должен быть позже last_verified_at')
        if (self.starts_at is None) != (self.ends_at is None): raise ValueError('Начало и конец сеанса задаются вместе')
        if self.starts_at and self.ends_at and self.starts_at>=self.ends_at: raise ValueError('Неверное время сеанса')
        if self.kind == 'event' and self.starts_at is None: raise ValueError('Для мероприятия обязателен сеанс')
        if self.kind == 'event' and self.parent_place_id is None:
            raise ValueError('Для мероприятия требуется проверенное место (parent_place_id)')
        if not self.address or not self.address.strip(): raise ValueError('Для публикации обязателен подтверждённый адрес')
        from urllib.parse import urlparse
        for name in ('source_url', 'booking_url'):
            url = getattr(self, name)
            if url:
                parsed = urlparse(url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError(f'{name}: требуется подтверждённый HTTPS URL')
        if not self.source_url: raise ValueError('Для публикации обязательна основная ссылка')
        from app.opening_hours import validate_schedule
        validate_schedule(self.opening_hours, self.calendar_exceptions)
        return self
