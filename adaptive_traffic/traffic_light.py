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
    ALL_RED_TO_PED = "ALL_RED_TO_PED"
    PED_GREEN = "PED_GREEN"
    PED_CLEARANCE = "PED_CLEARANCE"
    ALL_RED_AFTER_PED = "ALL_RED_AFTER_PED"


NEXT_STATE = {
    State.NS_GREEN: State.NS_YELLOW,
    State.NS_YELLOW: State.ALL_RED_TO_EW,
    State.ALL_RED_TO_EW: State.EW_GREEN,
    State.EW_GREEN: State.EW_YELLOW,
    State.EW_YELLOW: State.ALL_RED_TO_NS,
    State.ALL_RED_TO_NS: State.NS_GREEN,
    State.ALL_RED_TO_PED: State.PED_GREEN,
    State.PED_GREEN: State.PED_CLEARANCE,
    State.PED_CLEARANCE: State.ALL_RED_AFTER_PED,
}


class TrafficLight:
    def __init__(self, config: Config):
        self.config = config
        self._state = State.NS_GREEN
        self.state_started_at = 0.0
        self.pedestrian_requested_at: float | None = None
        self.pedestrian_started_at: float | None = None
        self.phase_after_pedestrian = Phase.EW

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

    def signal_for(self, direction: Direction) -> str:
        """Лампы выводятся из единственного состояния автомата, в том числе для GUI."""
        if direction in self.allowed_directions:
            return "GREEN"
        if self.state == State.NS_YELLOW and direction in PHASE_DIRECTIONS[Phase.NS]:
            return "YELLOW"
        if self.state == State.EW_YELLOW and direction in PHASE_DIRECTIONS[Phase.EW]:
            return "YELLOW"
        return "RED"

    def elapsed(self, time: float) -> float:
        return time - self.state_started_at

    @property
    def pedestrian_request(self) -> bool:
        # Один запрос остаётся активным до завершения всего безопасного цикла.
        return self.pedestrian_requested_at is not None

    @property
    def pedestrian_signal(self) -> str:
        return "GREEN" if self.state == State.PED_GREEN else "RED"

    def pedestrian_waiting_time(self, time: float) -> float:
        if not self.pedestrian_request:
            return 0.0
        end = self.pedestrian_started_at if self.pedestrian_started_at is not None else time
        return max(0.0, end - self.pedestrian_requested_at)

    def request_pedestrian(self, time: float) -> bool:
        if self.pedestrian_request:
            return False
        self.pedestrian_requested_at = time
        # Запрос, пришедший уже на общем красном, не теряется и не открывает
        # автомобили перед пешеходом. Начало красного интервала сохраняется.
        if self.state in (State.ALL_RED_TO_NS, State.ALL_RED_TO_EW):
            self.phase_after_pedestrian = Phase.NS if self.state == State.ALL_RED_TO_NS else Phase.EW
            self._state = State.ALL_RED_TO_PED
        return True

    def request_switch(self, time: float, next_phase: Phase | None = None) -> bool:
        if self.green_phase is None:
            return False
        if self.pedestrian_request and self.elapsed(time) < self.config.min_green:
            return False
        self.phase_after_pedestrian = next_phase or self.green_phase.other
        self._state = NEXT_STATE[self.state]
        self.state_started_at = time
        return True

    def advance(self, time: float):
        # Автоматически завершаются защитные и пешеходные интервалы.
        # Зелёный заканчивается исключительно по запросу контроллера.
        while self.green_phase is None:
            if self.state in (State.NS_YELLOW, State.EW_YELLOW):
                duration = self.config.yellow
            elif self.state == State.PED_GREEN:
                duration = self.config.ped_green
            elif self.state == State.PED_CLEARANCE:
                duration = self.config.ped_clearance
            else:
                duration = self.config.all_red
            if self.elapsed(time) < duration:
                break
            if self.state in (State.NS_YELLOW, State.EW_YELLOW) and self.pedestrian_request:
                self._state = State.ALL_RED_TO_PED
            elif self.state == State.ALL_RED_AFTER_PED:
                self._state = State.NS_GREEN if self.phase_after_pedestrian == Phase.NS else State.EW_GREEN
                self.pedestrian_requested_at = None
                self.pedestrian_started_at = None
            else:
                self._state = NEXT_STATE[self.state]
            self.state_started_at += duration
            if self.state == State.PED_GREEN:
                self.pedestrian_started_at = self.state_started_at
