import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import Action, AdaptiveController, AdaptiveV2Controller, Reason
from adaptive_traffic.models import Phase, PhaseStats, TrafficSnapshot, Direction
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation


@pytest.mark.parametrize('current,other,balanced', [(2, 3, False), (100, 1, False), (20, 22, True), (20, 60, False), (0, 30, False)])
def test_stabilization_depends_only_on_current_demand(current, other, balanced):
    c = Config()
    debug = AdaptiveV2Controller(c).debug_state(Phase.NS, TrafficSnapshot(PhaseStats(current), PhaseStats(other)))
    assert debug['balanced_load_detected'] == balanced
    assert debug['effective_min_green'] == (c.v2_balanced_min_green if balanced else c.min_green)


def test_small_advantage_held_but_large_advantage_releases_stabilization():
    v2 = AdaptiveV2Controller(Config())
    snapshot = TrafficSnapshot(PhaseStats(20), PhaseStats(22))
    assert AdaptiveController(Config()).decide(Phase.NS, 10, snapshot).reason == Reason.HIGHER_PRIORITY
    assert v2.decide(Phase.NS, 10, snapshot).action == Action.KEEP
    assert v2.decide(Phase.NS, 10, TrafficSnapshot(PhaseStats(20), PhaseStats(60))).reason == Reason.HIGHER_PRIORITY


@pytest.mark.parametrize('current,other,time,reason', [
    (PhaseStats(), PhaseStats(50), 10, Reason.EMPTY_CURRENT),
    (PhaseStats(20), PhaseStats(20), 45, Reason.MAX_GREEN),
    (PhaseStats(20, 70), PhaseStats(20, 70), 10, None),
    (PhaseStats(20, 70), PhaseStats(20, 70), 30, Reason.MAX_WAIT),
    (PhaseStats(100), PhaseStats(1, 60), 10, Reason.MAX_WAIT),
])
def test_rules_and_fairness(current, other, time, reason):
    result = AdaptiveV2Controller(Config()).decide(Phase.NS, time, TrafficSnapshot(current, other))
    assert result.reason == reason


def test_minimum_stays_within_configured_green_bounds():
    c = Config(min_green=10, max_green=15)
    assert AdaptiveV2Controller(c).debug_state(Phase.NS, TrafficSnapshot(PhaseStats(30), PhaseStats(30)))['effective_min_green'] == 15


def test_v2_pedestrian_safety_and_resume_priority():
    c = Config(simulation_duration=120)
    sim = Simulation(c, create_traffic_plan('BALANCED_HIGH', 42, 120), AdaptiveV2Controller(c), (5, 60))
    assert sim.phase_after_pedestrian_timeout(Phase.NS, TrafficSnapshot(PhaseStats(50), PhaseStats(1))) == Phase.NS
    assert sim.phase_after_pedestrian_timeout(Phase.NS, TrafficSnapshot(PhaseStats(1), PhaseStats(1))) == Phase.EW
    seen = set()
    while not sim.finished:
        sim.step()
        seen.add(sim.light.state.value)
        green = {d for d in Direction if sim.light.signal_for(d) == 'GREEN'}
        assert not (green.intersection({Direction.NORTH, Direction.SOUTH}) and green.intersection({Direction.EAST, Direction.WEST}))
        if sim.light.pedestrian_signal == 'GREEN':
            assert not green
    assert 'PED_GREEN' in seen and sim.all_pedestrians_served and sim.all_passed


@pytest.mark.parametrize('values', [{'v2_balance_ratio': 1.1}, {'v2_balance_ratio': 0}, {'v2_busy_queue_threshold': -1}, {'v2_balanced_min_green': 0}])
def test_invalid_v2_parameters(values):
    with pytest.raises(ValueError):
        Config(**values)
