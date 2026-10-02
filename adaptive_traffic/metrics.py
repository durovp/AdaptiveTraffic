"""Определения метрик общие для обоих контроллеров."""

import math
import statistics

from .simulation import SimulationResult


METRIC_NAMES = (
    "mean_wait", "median_wait", "p95_wait", "max_wait", "mean_queue", "max_queue",
    "vehicles_generated", "vehicles_passed", "active_generation_duration",
    "total_clearing_time", "number_of_switches",
)
RESULT_FIELDS = ("scenario", "controller", "seed", *METRIC_NAMES, "completed_successfully")


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
