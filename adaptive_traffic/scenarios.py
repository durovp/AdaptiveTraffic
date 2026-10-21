from .config import SCENARIO_PROFILES
from .traffic_demand import DemandSegment, TrafficPlan, generate_traffic_plan


def create_custom_plan(seed: int, duration: float, rates: tuple[float, float, float, float]) -> TrafficPlan:
    """The same Poisson generator; input rates are cars/minute in N/S/E/W order."""
    return generate_traffic_plan("CUSTOM", seed, duration, (DemandSegment(0, duration, tuple(rates)),))


def create_traffic_plan(scenario: str, seed: int, duration: float) -> TrafficPlan:
    scenario = scenario.upper()
    if scenario not in SCENARIO_PROFILES:
        raise ValueError(f"Unknown scenario: {scenario}")
    segments = []
    start = 0.0
    for end_fraction, rates in SCENARIO_PROFILES[scenario]:
        end = duration * end_fraction
        segments.append(DemandSegment(start, end, rates))
        start = end
    return generate_traffic_plan(scenario, seed, duration, tuple(segments))
