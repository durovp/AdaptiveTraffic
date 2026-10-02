from .config import SCENARIO_PROFILES
from .traffic_demand import DemandSegment, TrafficPlan, generate_traffic_plan


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
