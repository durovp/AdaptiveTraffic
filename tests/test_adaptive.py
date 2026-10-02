import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import Action, AdaptiveController, Reason, calculate_priority
from adaptive_traffic.models import Direction, Phase, PhaseStats, TrafficSnapshot
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.traffic_demand import Arrival, DemandSegment, TrafficPlan, generate_traffic_plan
from adaptive_traffic.traffic_light import NEXT_STATE


def decision(current, other, green_time=10, phase=Phase.NS):
    snapshot = TrafficSnapshot(current, other) if phase == Phase.NS else TrafficSnapshot(other, current)
    return AdaptiveController(Config()).decide(phase, green_time, snapshot)


def test_priority_formula():
    assert calculate_priority(5, 20, 10) == 7
    assert calculate_priority(5, 20, 5) == 9
    assert calculate_priority(0, 0, 10) == 0


@pytest.mark.parametrize("phase", list(Phase))
@pytest.mark.parametrize("green_time", [0, 1, 9])
def test_minimum_green_overrides_every_switch_reason(phase, green_time):
    assert decision(PhaseStats(), PhaseStats(100, 200), green_time, phase).action == Action.KEEP


@pytest.mark.parametrize("phase", list(Phase))
@pytest.mark.parametrize("current,other,time,reason", [
    (PhaseStats(), PhaseStats(1, 1), 10, Reason.EMPTY_CURRENT),
    # EMPTY_CURRENT имеет приоритет над MAX_WAIT и MAX_GREEN.
    (PhaseStats(), PhaseStats(1, 60), 45, Reason.EMPTY_CURRENT),
    (PhaseStats(100, 0), PhaseStats(1, 60), 10, Reason.MAX_WAIT),
    (PhaseStats(100, 0), PhaseStats(1, 60), 45, Reason.MAX_WAIT),
    (PhaseStats(100, 0), PhaseStats(1, 59), 45, Reason.MAX_GREEN),
    (PhaseStats(5, 0), PhaseStats(7, 0), 10, Reason.HIGHER_PRIORITY),
    (PhaseStats(5, 0), PhaseStats(5, 16), 10, Reason.HIGHER_PRIORITY),
])
def test_ordered_switch_rules(current, other, time, reason, phase):
    result = decision(current, other, time, phase)
    assert result.action == Action.REQUEST_SWITCH
    assert result.reason == reason


@pytest.mark.parametrize("current,other,time", [
    (PhaseStats(), PhaseStats(), 1000),
    (PhaseStats(1, 20), PhaseStats(), 1000),
    (PhaseStats(5, 0), PhaseStats(5, 15), 10),  # Равенство порогу.
    (PhaseStats(5, 0), PhaseStats(5, 14), 10),
    (PhaseStats(100, 0), PhaseStats(1, 59), 44),
])
def test_keep_without_sufficient_demand_or_priority(current, other, time):
    assert decision(current, other, time).action == Action.KEEP


def test_empty_adaptive_does_not_switch():
    config = Config(simulation_duration=100)
    plan = TrafficPlan("EMPTY", 0, 100, ())
    result = Simulation(config, plan, AdaptiveController(config)).run()
    assert result.completed_successfully
    assert result.switches == []


def test_max_wait_requests_safe_service_under_persistent_main_road_queue():
    config = Config(simulation_duration=100, max_green=120)
    arrivals = tuple(Arrival(i, Direction.NORTH, 0) for i in range(100)) + (
        Arrival(101, Direction.EAST, 0),
    )
    plan = TrafficPlan("STARVATION", 0, 100, arrivals)
    result = Simulation(config, plan, AdaptiveController(config)).run()
    assert result.switches[0]["reason"] == "MAX_WAIT"
    assert result.switches[0]["simulation_time"] == 60
    assert result.vehicles[-1].crossing_time == 66  # 60 + 3 + 1 + 2.
    assert result.completed_successfully


def test_adaptive_integration_preserves_safe_order_and_minimum_green():
    config = Config(simulation_duration=200)
    plan = generate_traffic_plan("TEST", 42, 200, (DemandSegment(0, 200, (15, 15, 15, 15)),))
    simulation = Simulation(config, plan, AdaptiveController(config))
    previous_state = simulation.light.state
    previous_green_start = 0
    while simulation.time < 200 or not simulation.all_passed:
        assert simulation.time < 200 + config.drain_timeout
        time = simulation.time
        previous_phase = simulation.light.green_phase
        simulation.step()
        state = simulation.light.state
        if state != previous_state:
            assert state == NEXT_STATE[previous_state]
            if previous_phase is not None:
                assert time - previous_green_start >= config.min_green
            if simulation.light.green_phase is not None:
                previous_green_start = time
        assert len(simulation.light.allowed_directions) in (0, 2)
        previous_state = state
