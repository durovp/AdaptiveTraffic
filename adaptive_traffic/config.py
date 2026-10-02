"""Все параметры времени — в секундах; интенсивности — в авто/мин."""

from dataclasses import dataclass
import math


# Доли длительности, до которых действуют заданные интенсивности.
# Порядок интенсивностей: NORTH, SOUTH, EAST, WEST; единицы: авто/мин.
SCENARIO_PROFILES = {
    "BALANCED_LOW": ((1.0, (4.0, 4.0, 4.0, 4.0)),),
    "BALANCED_HIGH": ((1.0, (12.0, 12.0, 12.0, 12.0)),),
    "NS_HEAVY": ((1.0, (20.0, 20.0, 4.0, 4.0)),),
    "EW_HEAVY": ((1.0, (4.0, 4.0, 20.0, 20.0)),),
    "RUSH_HOUR": (
        (0.5, (6.0, 6.0, 6.0, 6.0)),
        (1.0, (18.0, 18.0, 18.0, 18.0)),
    ),
    "CHANGING_FLOW": (
        (1 / 3, (18.0, 18.0, 4.0, 4.0)),
        (2 / 3, (8.0, 8.0, 8.0, 8.0)),
        (1.0, (4.0, 4.0, 18.0, 18.0)),
    ),
    "MINOR_ROAD": ((1.0, (18.0, 18.0, 0.5, 0.5)),),
}


@dataclass(frozen=True)
class Config:
    time_step: float = 1.0
    vehicle_headway: float = 2.0
    fixed_green: float = 30.0
    yellow: float = 3.0
    all_red: float = 1.0
    min_green: float = 10.0
    max_green: float = 45.0
    max_wait: float = 60.0
    wait_equivalent: float = 10.0
    switch_margin: float = 1.5
    simulation_duration: float = 1800.0
    # Дополнительное время ПОСЛЕ окончания генерации.
    drain_timeout: float = 1800.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
            if name != "switch_margin" and value == 0:
                raise ValueError(f"{name} must be positive")
        if self.min_green > self.max_green:
            raise ValueError("min_green must not exceed max_green")
        for name in (
            "vehicle_headway", "fixed_green", "yellow", "all_red",
            "min_green", "max_green", "max_wait",
            "simulation_duration", "drain_timeout",
        ):
            steps = getattr(self, name) / self.time_step
            if not math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"{name} must be a multiple of time_step")
