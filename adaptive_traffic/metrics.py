"""Определения метрик общие для обоих контроллеров."""

import math
import statistics
from collections import Counter

from .controllers import Reason
from .simulation import SimulationResult


METRIC_NAMES = (
    "mean_wait", "median_wait", "p95_wait", "max_wait", "mean_queue", "max_queue",
    "vehicles_generated", "vehicles_passed", "active_generation_duration",
    "total_clearing_time", "number_of_switches",
)
RESULT_FIELDS = ("scenario", "controller", "seed", *METRIC_NAMES, "completed_successfully")
DIAGNOSTIC_FIELDS = (
    "scenario", "controller", "seed",
    *(f"switches_{reason.value}" for reason in Reason),
    "average_green_duration", "total_yellow_time", "total_all_red_time",
    "transition_time_fraction", "completed_successfully",
)


def percentile(values: list[float], percent: float) -> float:
    """Линейная интерполяция: индекс = (n - 1) * percent / 100."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must lie in [0, 100]")
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def calculate_metrics(result: SimulationResult) -> dict:
    # В незавершённом опыте это только обслуженные машины! Такой запуск
    # сохраняется с флагом False и не допускается в итоговое сравнение.
    waits = [vehicle.waiting_time for vehicle in result.vehicles if vehicle.crossing_time is not None]
    return {
        "scenario": result.plan.scenario,
        "controller": result.controller,
        "seed": result.plan.seed,
        "mean_wait": statistics.mean(waits) if waits else 0.0,
        "median_wait": statistics.median(waits) if waits else 0.0,
        "p95_wait": percentile(waits, 95),
        "max_wait": max(waits, default=0.0),
        "mean_queue": statistics.mean(result.queue_samples) if result.queue_samples else 0.0,
        "max_queue": max(result.queue_samples, default=0),
        "vehicles_generated": len(result.vehicles),
        "vehicles_passed": len(waits),
        "active_generation_duration": result.plan.duration,
        "total_clearing_time": result.elapsed_time,
        "number_of_switches": len(result.switches),
        "completed_successfully": result.completed_successfully,
    }


def calculate_diagnostics(result: SimulationResult) -> dict:
    reasons = Counter(switch["reason"] for switch in result.switches)
    yellow = sum(duration for state, duration in result.state_durations.items() if state.endswith("_YELLOW"))
    all_red = sum(duration for state, duration in result.state_durations.items() if state.startswith("ALL_RED"))
    return {
        "scenario": result.plan.scenario, "controller": result.controller, "seed": result.plan.seed,
        **{f"switches_{reason.value}": reasons[reason.value] for reason in Reason},
        "average_green_duration": statistics.mean(result.green_durations) if result.green_durations else 0.0,
        "total_yellow_time": yellow,
        "total_all_red_time": all_red,
        # PED_GREEN/PED_CLEARANCE не являются жёлтым или общим красным.
        "transition_time_fraction": (yellow + all_red) / result.elapsed_time if result.elapsed_time else 0.0,
        "completed_successfully": result.completed_successfully,
    }
