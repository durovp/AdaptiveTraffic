"""Генерация входных данных не знает, какой контроллер будет их обслуживать."""

from dataclasses import dataclass
import math
import random

from .models import Direction


@dataclass(frozen=True)
class Arrival:
    id: int
    direction: Direction
    arrival_time: float


@dataclass(frozen=True)
class DemandSegment:
    start: float
    end: float
    # Порядок: NORTH, SOUTH, EAST, WEST.
    rates: tuple[float, float, float, float]


@dataclass(frozen=True)
class TrafficPlan:
    scenario: str
    seed: int
    duration: float
    arrivals: tuple[Arrival, ...]

    def __post_init__(self):
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("TrafficPlan duration must be positive and finite")
        if not isinstance(self.arrivals, tuple):
            raise ValueError("arrivals must be an immutable tuple")
        ids = set()
        previous_time = -1.0
        for arrival in self.arrivals:
            if not isinstance(arrival.direction, Direction):
                raise ValueError("Unknown direction")
            if not 0 <= arrival.arrival_time < self.duration:
                raise ValueError("Arrival must lie in [0, duration)")
            if arrival.arrival_time < previous_time:
                raise ValueError("Arrivals must be sorted by time")
            if arrival.id in ids:
                raise ValueError("Vehicle IDs must be unique")
            ids.add(arrival.id)
            previous_time = arrival.arrival_time


def generate_traffic_plan(
    scenario: str, seed: int, duration: float, segments: tuple[DemandSegment, ...]
) -> TrafficPlan:
    """Пуассоновский поток: интервалы независимы и экспоненциальны.

    Для каждого участка постоянной интенсивности генерируем новый поток.
    Интервал может быть меньше секунды: несколько машин за шаг допустимы.
    Локальный Random не зависит от случайных чисел в других частях программы.
    """
    random_generator = random.Random(seed)
    raw_arrivals = []
    previous_end = 0.0
    for segment in segments:
        if not 0 <= segment.start < segment.end <= duration:
            raise ValueError("Invalid demand segment boundaries")
        if segment.start != previous_end:
            raise ValueError("Demand segments must cover the duration without gaps")
        if len(segment.rates) != 4:
            raise ValueError("Provide one rate per direction")
        for direction, rate in zip(Direction, segment.rates):
            if not math.isfinite(rate) or rate < 0:
                raise ValueError("Arrival rates must be finite and nonnegative")
            if rate == 0:
                continue
            arrival_time = segment.start
            while True:
                arrival_time += random_generator.expovariate(rate / 60.0)
                if arrival_time >= segment.end:
                    break
                raw_arrivals.append((arrival_time, direction))
        previous_end = segment.end
    if previous_end != duration:
        raise ValueError("Demand segments must cover the whole duration")
    raw_arrivals.sort(key=lambda item: item[0])
    arrivals = tuple(
        Arrival(index, direction, time)
        for index, (time, direction) in enumerate(raw_arrivals, start=1)
    )
    return TrafficPlan(scenario, seed, duration, arrivals)
