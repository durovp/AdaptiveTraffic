"""Контроллеры выдают только решение; лампами управляет TrafficLight."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .config import Config
from .models import Phase, PhaseStats, TrafficSnapshot


class Action(str, Enum):
    KEEP = "KEEP"
    REQUEST_SWITCH = "REQUEST_SWITCH"


class Reason(str, Enum):
    FIXED_TIMER = "FIXED_TIMER"
    EMPTY_CURRENT = "EMPTY_CURRENT"
    MAX_WAIT = "MAX_WAIT"
    MAX_GREEN = "MAX_GREEN"
    HIGHER_PRIORITY = "HIGHER_PRIORITY"
    PED_MAX_WAIT = "PED_MAX_WAIT"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason | None = None


class Controller(Protocol):
    name: str

    def decide(
        self, current_phase: Phase, green_time: float, snapshot: TrafficSnapshot
    ) -> Decision: ...


def calculate_priority(queue: int, oldest_wait: float, wait_equivalent: float) -> float:
    return queue + oldest_wait / wait_equivalent


class FixedController:
    name = "FIXED"

    def __init__(self, config: Config):
        self.config = config

    def decide(
        self, current_phase: Phase, green_time: float, snapshot: TrafficSnapshot
    ) -> Decision:
        # Очереди и ожидания намеренно не участвуют в решении FIXED.
        if green_time >= self.config.fixed_green:
            return Decision(Action.REQUEST_SWITCH, Reason.FIXED_TIMER)
        return Decision(Action.KEEP)


class AdaptiveController:
    name = "ADAPTIVE"

    def __init__(self, config: Config):
        self.config = config

    def decide(
        self, current_phase: Phase, green_time: float, snapshot: TrafficSnapshot
    ) -> Decision:
        current = snapshot.for_phase(current_phase)
        other = snapshot.for_phase(current_phase.other)

        # 1. Даже очень большой встречный поток не отменяет минимальный зелёный.
        if green_time < self.config.min_green:
            return Decision(Action.KEEP)

        # 2. После минимума нет смысла держать пустую сторону при наличии спроса.
        if current.queue == 0 and other.queue > 0:
            return Decision(Action.REQUEST_SWITCH, Reason.EMPTY_CURRENT)

        # 3. Защита редких машин от бесконечного ожидания (starvation).
        if other.oldest_wait >= self.config.max_wait:
            return Decision(Action.REQUEST_SWITCH, Reason.MAX_WAIT)

        # 4. Максимум ограничивает зелёный только при спросе другой стороны.
        if green_time >= self.config.max_green and other.queue > 0:
            return Decision(Action.REQUEST_SWITCH, Reason.MAX_GREEN)

        # 5. Строгое >: равенства с порогом недостаточно для переключения.
        current_priority = calculate_priority(
            current.queue, current.oldest_wait, self.config.wait_equivalent
        )
        other_priority = calculate_priority(
            other.queue, other.oldest_wait, self.config.wait_equivalent
        )
        if other_priority > current_priority + self.config.switch_margin:
            return Decision(Action.REQUEST_SWITCH, Reason.HIGHER_PRIORITY)
        return Decision(Action.KEEP)


def calculate_queue_priority(queue: int) -> float:
    return float(queue)


class QueueOnlyController:
    """Контрольный вариант: те же ограничения, но обычный приоритет без ожидания.

    Правила выписаны явно, чтобы исходный AdaptiveController оставался неизменным.
    MAX_WAIT по-прежнему отдельно защищает редкое направление.
    """

    name = "QUEUE_ONLY"

    def __init__(self, config: Config):
        self.config = config

    def decide(self, current_phase: Phase, green_time: float, snapshot: TrafficSnapshot) -> Decision:
        current = snapshot.for_phase(current_phase)
        other = snapshot.for_phase(current_phase.other)
        if green_time < self.config.min_green:
            return Decision(Action.KEEP)
        if current.queue == 0 and other.queue > 0:
            return Decision(Action.REQUEST_SWITCH, Reason.EMPTY_CURRENT)
        if other.oldest_wait >= self.config.max_wait:
            return Decision(Action.REQUEST_SWITCH, Reason.MAX_WAIT)
        if green_time >= self.config.max_green and other.queue > 0:
            return Decision(Action.REQUEST_SWITCH, Reason.MAX_GREEN)
        if calculate_queue_priority(other.queue) > calculate_queue_priority(current.queue) + self.config.switch_margin:
            return Decision(Action.REQUEST_SWITCH, Reason.HIGHER_PRIORITY)
        return Decision(Action.KEEP)


CONTROLLERS = {"FIXED": FixedController, "QUEUE_ONLY": QueueOnlyController, "ADAPTIVE": AdaptiveController}


def phase_priority(controller_name: str, stats: PhaseStats, config: Config) -> float:
    """Приоритет для журнала/демонстрации. FIXED его в своих решениях не использует."""
    if controller_name == "QUEUE_ONLY":
        return calculate_queue_priority(stats.queue)
    return calculate_priority(stats.queue, stats.oldest_wait, config.wait_equivalent)
