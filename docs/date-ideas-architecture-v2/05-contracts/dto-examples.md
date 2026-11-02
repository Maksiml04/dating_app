# Типизированные доменные DTO (нормативная семантика, пример Python)

Приведённые типы описывают **внутренние контракты приложения**, а не готовые производственные модули или окончательную SQL-схему. Недоверенный вывод LLM проверяется через Pydantic и доменные ограничения. Для хранения используется UTC, для интерпретации дат московского пилота — Europe/Moscow.

```python
from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field

class Strictness(str, Enum):
    hard = "hard"            # явное условие: не ослаблять молча
    soft = "soft"            # предпочтение: влияет только на ранжирование
    unspecified = "unspecified"  # нет фильтра; не путать с неизвестными данными каталога

class Source(str, Enum):
    current_message = "current_message"
    explicit_profile = "explicit_profile"
    session_context = "session_context"

class Constraint(BaseModel):
    value: str | int | list[str] | None
    strictness: Strictness
    source: Source | None = None

class SearchRequest(BaseModel):
    request_id: UUID
    generation_id: int = Field(ge=1)
    user_id: UUID
    time_start_utc: datetime | None = None
    time_end_utc: datetime | None = None
    budget_total_kopeks: Constraint | None = None  # общая сумма на двоих
    metro_ids: Constraint | None = None
    district_ids: Constraint | None = None
    category_ids: Constraint | None = None
    indoor_outdoor: Constraint | None = None
    vibe_terms: list[str] = Field(default_factory=list)
    exclude_canonical_ids: list[UUID] = Field(default_factory=list)

class RecommendationCandidate(BaseModel):
    canonical_id: UUID
    publication_version_id: UUID
    occurrence_id: UUID | None = None
    offer_id: UUID | None = None
    verified_at: datetime
    valid_until: datetime
    price_total_kopeks: int | None = None
    price_is_bounded: bool
    reason_codes: list[str] = Field(default_factory=list)

class DeliveryIntent(BaseModel):
    delivery_id: UUID
    request_id: UUID
    generation_id: int
    user_id: UUID
    candidate_ids: list[UUID]  # ID сохранённых кандидатов из устойчивого плана
    expires_at: datetime
    status: str  # pending, sending, delivered, ambiguous, failed, cancelled
```

## Правила доменной валидации
- `SearchRequest` нормализуется Python-кодом; вывод LLM нельзя принимать без проверки.
- Начало интервала должно предшествовать концу; даты содержат часовой пояс.
- `generation_id` увеличивается монотонно; проверяется перед записью, отправкой и обработкой callback.
- Жёсткие условия важнее мягких предпочтений; конфликты требуют уточнения.
- Цена без надёжной верхней границы не проходит жёсткий бюджет.
- `RecommendationCandidate` должен ссылаться на действительную опубликованную версию.
- `DeliveryIntent` с истёкшим TTL или устаревшим поколением не отправляется.
