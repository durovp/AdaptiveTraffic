"""Безопасная конечная машина состояний, общая для всех контроллеров."""

from enum import Enum

from .config import Config
from .models import Direction, PHASE_DIRECTIONS, Phase


class State(str, Enum):
    NS_GREEN = "NS_GREEN"
    NS_YELLOW = "NS_YELLOW"
    ALL_RED_TO_EW = "ALL_RED_TO_EW"
    EW_GREEN = "EW_GREEN"
    EW_YELLOW = "EW_YELLOW"
    ALL_RED_TO_NS = "ALL_RED_TO_NS"


NEXT_STATE = {
    State.NS_GREEN: State.NS_YELLOW,
    State.NS_YELLOW: State.ALL_RED_TO_EW,
    State.ALL_RED_TO_EW: State.EW_GREEN,
    State.EW_GREEN: State.EW_YELLOW,
    State.EW_YELLOW: State.ALL_RED_TO_NS,
    State.ALL_RED_TO_NS: State.NS_GREEN,
}


class TrafficLight:
    def __init__(self, config: Config):
        self.config = config
        self._state = State.NS_GREEN
        self.state_started_at = 0.0

    @property
    def state(self) -> State:
        return self._state

    @property
    def green_phase(self) -> Phase | None:
        if self.state == State.NS_GREEN:
            return Phase.NS
        if self.state == State.EW_GREEN:
            return Phase.EW
        return None

    @property
    def allowed_directions(self) -> tuple[Direction, ...]:
        # На жёлтом и общем красном никто не пересекает стоп-линию.
        return PHASE_DIRECTIONS.get(self.green_phase, ())

    def elapsed(self, time: float) -> float:
        return time - self.state_started_at

    def request_switch(self, time: float) -> bool:
        if self.green_phase is None:
            return False
        self._state = NEXT_STATE[self.state]
        self.state_started_at = time
        return True

    def advance(self, time: float):
        # Автоматически завершаются только защитные интервалы.
        # Зелёный заканчивается исключительно по запросу контроллера.
        while self.green_phase is None:
            duration = (
                self.config.yellow
                if self.state in (State.NS_YELLOW, State.EW_YELLOW)
                else self.config.all_red
            )
            if self.elapsed(time) < duration:
                break
            self._state = NEXT_STATE[self.state]
            self.state_started_at += duration
