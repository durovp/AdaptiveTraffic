from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class Phase(str, Enum):
    NS = "NS"
    EW = "EW"

    @property
    def other(self):
        return Phase.EW if self == Phase.NS else Phase.NS


PHASE_DIRECTIONS = {
    Phase.NS: (Direction.NORTH, Direction.SOUTH),
    Phase.EW: (Direction.EAST, Direction.WEST),
}


@dataclass
class Vehicle:
    id: int
    direction: Direction
    arrival_time: float
    crossing_time: float | None = None

    @property
    def waiting_time(self) -> float | None:
        if self.crossing_time is None:
            return None
        return self.crossing_time - self.arrival_time


@dataclass(frozen=True)
class PhaseStats:
    queue: int = 0
    oldest_wait: float = 0.0


@dataclass(frozen=True)
class TrafficSnapshot:
    ns: PhaseStats
    ew: PhaseStats

    def for_phase(self, phase: Phase) -> PhaseStats:
        return self.ns if phase == Phase.NS else self.ew
