"""Транспортный порт (Ports & Adapters): ядро не знает про Telegram.

Transport — абстракция доставки; Telegram, web-API и тестовые реализации
подключаются снаружи. Ядро возвращает OutboundMessage, адаптер решает, как
отправить (sendMessage, sendPhoto, кнопки и т.д.).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.agent import OutboundMessage


@dataclass
class DeliveryReceipt:
    """Подтверждение доставки: успешна только после ответа транспорта."""
    ok: bool
    delivered_at: str
    ref: str | None = None
    error: str | None = None


class Transport(ABC):
    """Порт доставки исходящих сообщений для одного пользователя."""

    @abstractmethod
    def send(self, user_ref: str, message: OutboundMessage) -> DeliveryReceipt: ...

    def send_all(self, user_ref: str, messages: list[OutboundMessage]) -> list[DeliveryReceipt]:
        return [self.send(user_ref, message) for message in messages]


class InMemoryTransport(Transport):
    """Тестовая/демо-реализация: сохраняет сообщения в памяти."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage, DeliveryReceipt]] = []

    def send(self, user_ref: str, message: OutboundMessage) -> DeliveryReceipt:
        receipt = DeliveryReceipt(ok=True,
                                  delivered_at=datetime.now(UTC).isoformat(),
                                  ref=f'inmem:{len(self.sent) + 1}')
        self.sent.append((user_ref, message, receipt))
        return receipt

    def texts(self, user_ref: str | None = None) -> list[str]:
        return [m.text for ref, m, _ in self.sent if user_ref is None or ref == user_ref]

    def buttons(self, user_ref: str | None = None) -> list[list[str]]:
        return [[b.label for b in row] for ref, m, _ in self.sent
                if user_ref is None or ref == user_ref for row in m.buttons]
