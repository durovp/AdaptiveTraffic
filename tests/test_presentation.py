import subprocess
import sys

import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import AdaptiveController, FixedController, QueueOnlyController
from adaptive_traffic.metrics import calculate_metrics
from adaptive_traffic.models import Direction
from adaptive_traffic.presentation import GuiSession, get_view_state
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation


@pytest.mark.parametrize("speed", [1, 5, 20])
@pytest.mark.parametrize("controller", [FixedController, QueueOnlyController, AdaptiveController])
def test_gui_clock_matches_headless_for_all_speeds(speed, controller):
    config = Config(simulation_duration=120)
    plan = create_traffic_plan("NS_HEAVY", 42, 120)
    expected = Simulation(config, plan, controller(config)).run()
    session = GuiSession(config, plan, controller.name)
    session.speed = speed
    while not session.simulation.finished:
        session.advance_wall_time(0.05)
        get_view_state(session.simulation)
    actual = session.simulation.result()
    assert calculate_metrics(actual) == calculate_metrics(expected)
    assert actual.switches == expected.switches
    assert actual.vehicles == expected.vehicles


def test_view_reports_model_state_and_does_not_advance_simulation():
    config = Config(simulation_duration=80)
    session = GuiSession(config, create_traffic_plan("NS_HEAVY", 42, 80), "FIXED")
    session.advance_wall_time(31)
    simulation = session.simulation
    view = get_view_state(simulation)
    assert view["time"] == 31
    assert view["phase"] == "NS_YELLOW"
    assert view["phase_elapsed"] == 1
    assert view["decision"] == "SWITCH"
    assert view["last_switch_reason"] == "FIXED_TIMER"
    assert view["priorities"] == {"NS": None, "EW": None}
    assert view["signals"][Direction.NORTH] == "YELLOW"
    assert view["signals"][Direction.EAST] == "RED"
    assert view["queue_NS"] == len(simulation.queues[Direction.NORTH]) + len(simulation.queues[Direction.SOUTH])
    assert get_view_state(simulation) == view


def test_adaptive_priorities_and_departure_events_come_from_model():
    config = Config(simulation_duration=80)
    session = GuiSession(config, create_traffic_plan("NS_HEAVY", 42, 80), "ADAPTIVE")
    session.advance_wall_time(8)
    view = get_view_state(session.simulation)
    assert view["priorities"]["NS"] == view["queue_NS"] + view["wait_NS"] / 10
    assert all(vehicle.crossing_time is not None for vehicle in session.crossings)
    assert all(vehicle not in session.simulation.queues[vehicle.direction] for vehicle in session.crossings)


def test_pause_restart_and_controller_change_reuse_same_plan():
    config = Config(simulation_duration=80)
    plan = create_traffic_plan("NS_HEAVY", 42, 80)
    session = GuiSession(config, plan, "FIXED")
    session.advance_wall_time(15)
    session.paused = True
    view = get_view_state(session.simulation)
    session.advance_wall_time(100)
    assert get_view_state(session.simulation) == view
    session.restart("ADAPTIVE")
    assert session.simulation.plan is plan
    assert session.simulation.time == 0
    assert session.simulation.controller.name == "ADAPTIVE"
    assert session.crossings == []
    session.restart()
    assert session.simulation.plan is plan


def test_headless_imports_do_not_load_pygame():
    command = "import sys; import adaptive_traffic.experiments; import adaptive_traffic.presentation; assert 'pygame' not in sys.modules"
    subprocess.run([sys.executable, "-c", command], check=True)


def test_visualize_cli_uses_shared_session_without_opening_window(monkeypatch):
    from adaptive_traffic import gui
    from adaptive_traffic.experiments import main
    sessions = []
    def capture(session):
        sessions.append(session)
        return 0
    monkeypatch.setattr(gui, "run_gui", capture)
    assert main(["--visualize", "--controller", "queue_only", "--scenario", "ns_heavy",
                 "--seed", "42", "--duration", "120", "--pedestrian-times", "5", "60"]) == 0
    assert sessions[0].simulation.controller.name == "QUEUE_ONLY"
    assert sessions[0].plan == create_traffic_plan("NS_HEAVY", 42, 120)
    assert sessions[0].simulation.pedestrian_times == (5, 60)
