import csv

import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import Action, AdaptiveController, QueueOnlyController, Reason, calculate_queue_priority
from adaptive_traffic.experiments import run_comparison, run_experiments
from adaptive_traffic.models import Phase, PhaseStats, TrafficSnapshot
from adaptive_traffic.presentation import GuiSession, get_view_state
from adaptive_traffic.scenarios import create_traffic_plan


def test_queue_priority_and_waiting_component_are_distinct():
    assert calculate_queue_priority(5) == 5
    controller = QueueOnlyController(Config())
    for wait in (0, 20, 59):
        snapshot = TrafficSnapshot(PhaseStats(5, 0), PhaseStats(5, wait))
        assert controller.decide(Phase.NS, 10, snapshot).action == Action.KEEP
    assert AdaptiveController(Config()).decide(Phase.NS, 10, snapshot).reason == Reason.HIGHER_PRIORITY


@pytest.mark.parametrize("phase", list(Phase))
@pytest.mark.parametrize("current,other,time,reason", [
    (PhaseStats(), PhaseStats(100, 100), 9, None),
    (PhaseStats(), PhaseStats(1, 60), 10, Reason.EMPTY_CURRENT),
    (PhaseStats(100, 0), PhaseStats(1, 60), 10, Reason.MAX_WAIT),
    (PhaseStats(100, 0), PhaseStats(1, 60), 45, Reason.MAX_WAIT),
    (PhaseStats(100, 0), PhaseStats(1, 59), 45, Reason.MAX_GREEN),
    (PhaseStats(5, 0), PhaseStats(7, 0), 10, Reason.HIGHER_PRIORITY),
    (PhaseStats(5, 0), PhaseStats(6, 59), 10, None),
    (PhaseStats(5, 0), PhaseStats(), 100, None),
    (PhaseStats(), PhaseStats(), 100, None),
])
def test_queue_only_rules(current, other, time, reason, phase):
    snapshot = TrafficSnapshot(current, other) if phase == Phase.NS else TrafficSnapshot(other, current)
    result = QueueOnlyController(Config()).decide(phase, time, snapshot)
    assert result.reason == reason
    assert result.action == (Action.KEEP if reason is None else Action.REQUEST_SWITCH)


def test_switch_margin_is_strict():
    controller = QueueOnlyController(Config(switch_margin=2))
    assert controller.decide(Phase.NS, 10, TrafficSnapshot(PhaseStats(5), PhaseStats(7))).action == Action.KEEP


def test_three_controllers_share_plan_and_write_csv(tmp_path):
    config = Config(simulation_duration=120)
    plan = create_traffic_plan("NS_HEAVY", 42, 120)
    results = run_comparison(config, plan, ("FIXED", "QUEUE_ONLY", "ADAPTIVE"))
    assert len(results) == 3
    assert all(result.plan is plan and result.completed_successfully for result in results)
    path = run_experiments(config, ["NS_HEAVY"], [42], tmp_path / "three", "all")
    with path.open() as stream:
        assert {row["controller"] for row in csv.DictReader(stream)} == {"FIXED", "QUEUE_ONLY", "ADAPTIVE"}


def test_queue_only_gui_priority_and_restart():
    config = Config(simulation_duration=80)
    session = GuiSession(config, create_traffic_plan("NS_HEAVY", 42, 80), "QUEUE_ONLY")
    session.advance_wall_time(30)
    view = get_view_state(session.simulation)
    assert view["priorities"]["NS"] == view["queue_NS"]
    assert view["priorities"]["EW"] == view["queue_EW"]
    session.restart("ADAPTIVE")
    assert session.simulation.plan is session.plan
