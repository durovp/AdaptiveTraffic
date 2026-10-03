import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import CONTROLLERS, AdaptiveController, FixedController
from adaptive_traffic.metrics import calculate_metrics
from adaptive_traffic.models import Direction, Phase, PhaseStats, TrafficSnapshot
from adaptive_traffic.presentation import GuiSession, get_view_state
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.traffic_demand import Arrival, TrafficPlan
from adaptive_traffic.traffic_light import State, TrafficLight


@pytest.mark.parametrize("initial", [Phase.NS, Phase.EW])
def test_safe_pedestrian_path_both_directions_and_duplicate_requests(initial):
    light = TrafficLight(Config())
    start = 0
    if initial == Phase.EW:
        light.request_switch(30)
        light.advance(34)
        start = 34
    assert light.request_pedestrian(start + 1)
    assert not light.request_pedestrian(start + 2)
    assert not light.request_switch(start + 9)
    assert light.green_phase == initial
    assert light.request_switch(start + 10)
    yellow = State.NS_YELLOW if initial == Phase.NS else State.EW_YELLOW
    for offset, expected in ((10, yellow), (12, yellow), (13, State.ALL_RED_TO_PED),
                             (14, State.PED_GREEN), (23, State.PED_GREEN),
                             (24, State.PED_CLEARANCE), (26, State.PED_CLEARANCE),
                             (27, State.ALL_RED_AFTER_PED)):
        light.advance(start + offset)
        assert light.state == expected
        assert light.allowed_directions == ()
        assert not light.request_pedestrian(start + offset)
        assert light.pedestrian_request
        if expected in (State.PED_GREEN, State.PED_CLEARANCE, State.ALL_RED_AFTER_PED):
            assert all(light.signal_for(direction) == "RED" for direction in Direction)
    light.advance(start + 28)
    assert light.green_phase == initial.other
    assert not light.pedestrian_request
    assert light.request_pedestrian(start + 29)  # Следующий независимый запрос допустим.


@pytest.mark.parametrize("request_time", [31, 33])
def test_request_during_yellow_or_all_red_is_not_lost(request_time):
    light = TrafficLight(Config())
    light.request_switch(30)
    light.advance(request_time)
    light.request_pedestrian(request_time)
    light.advance(34)
    assert light.state == State.PED_GREEN
    assert light.phase_after_pedestrian == Phase.EW


@pytest.mark.parametrize("controller", CONTROLLERS.values())
def test_pedestrian_timeout_serves_empty_road_and_returns_to_cars(controller):
    config = Config(simulation_duration=80, fixed_green=100)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 80, ()), controller(config), (0,))
    states = []
    while not simulation.finished:
        simulation.step()
        states.append(simulation.light.state)
        if simulation.light.pedestrian_signal == "GREEN":
            assert simulation.light.green_phase is None
            assert all(simulation.light.signal_for(direction) == "RED" for direction in Direction)
    assert states[39] == State.NS_GREEN
    assert states[40] == State.NS_YELLOW
    assert states[43] == State.ALL_RED_TO_PED
    assert states[44] == State.PED_GREEN
    assert states[54] == State.PED_CLEARANCE
    assert states[57] == State.ALL_RED_AFTER_PED
    assert states[58] == State.EW_GREEN
    assert simulation.switches[0]["reason"] == "PED_MAX_WAIT"
    assert simulation.result().completed_successfully


def test_normal_fixed_switch_inserts_pedestrian_and_keeps_original_destination():
    config = Config(simulation_duration=80)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 80, ()), FixedController(config), (5, 6, 29, 35))
    states = []
    while not simulation.finished:
        simulation.step()
        states.append(simulation.light.state)
    assert states[30] == State.NS_YELLOW
    assert states[34] == State.PED_GREEN
    assert states[48] == State.EW_GREEN
    assert sum(state == State.PED_GREEN for state in states) == config.ped_green
    assert simulation.switches[0]["reason"] == "FIXED_TIMER"
    assert simulation.switches[0]["new_phase"] == "EW"


def test_pedestrian_does_not_break_minimum_even_with_short_fixed_timer():
    config = Config(simulation_duration=40, fixed_green=5, ped_max_wait=1)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 40, ()), FixedController(config), (0,))
    simulation.run()
    assert simulation.switches[0]["simulation_time"] == 10


@pytest.mark.parametrize("controller,expected", [("FIXED", Phase.EW), ("QUEUE_ONLY", Phase.NS), ("ADAPTIVE", Phase.EW)])
def test_timeout_return_phase_uses_controller_priority(controller, expected):
    config = Config(simulation_duration=80)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 80, ()), CONTROLLERS[controller](config))
    snapshot = TrafficSnapshot(PhaseStats(5, 0), PhaseStats(4, 20))
    assert simulation.phase_after_pedestrian_timeout(Phase.NS, snapshot) == expected


def test_simulation_drains_pending_pedestrian_even_without_cars():
    config = Config(simulation_duration=1)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 1, ()), AdaptiveController(config), (0,))
    result = simulation.run()
    assert result.elapsed_time > 1
    assert result.completed_successfully
    assert not simulation.light.pedestrian_request
    assert simulation.light.green_phase is not None


def test_timeout_during_pending_pedestrian_is_incomplete():
    config = Config(simulation_duration=1, drain_timeout=1)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 1, ()), AdaptiveController(config), (0,))
    result = simulation.run()
    assert result.elapsed_time == 2
    assert not result.completed_successfully
    assert simulation.light.pedestrian_request


def test_gui_pedestrian_state_and_pause():
    config = Config(simulation_duration=80)
    session = GuiSession(config, TrafficPlan("EMPTY", 0, 80, ()), "ADAPTIVE")
    assert session.simulation.request_pedestrian()
    session.advance_wall_time(20)
    view = get_view_state(session.simulation)
    assert view["pedestrian_request"] and view["pedestrian_wait"] == 20
    session.paused = True
    assert not session.simulation.request_pedestrian()
    session.advance_wall_time(100)
    assert get_view_state(session.simulation) == view
    session.paused = False
    session.advance_wall_time(25)
    view = get_view_state(session.simulation)
    assert view["phase"] == "PED_GREEN"
    assert view["pedestrian_signal"] == "GREEN"
    assert view["pedestrian_wait"] == 44
    session.restart()
    assert not get_view_state(session.simulation)["pedestrian_request"]


@pytest.mark.parametrize("controller", CONTROLLERS.values())
@pytest.mark.parametrize("speed", [1, 5, 20])
def test_pedestrian_gui_and_headless_match(controller, speed):
    config = Config(simulation_duration=120)
    plan = create_traffic_plan("NS_HEAVY", 42, 120)
    requests = (0, 5, 30, 70)
    headless = Simulation(config, plan, controller(config), requests).run()
    session = GuiSession(config, plan, controller.name, requests)
    session.speed = speed
    while not session.simulation.finished:
        session.advance_wall_time(0.1)
        view = get_view_state(session.simulation)
        if view["pedestrian_signal"] == "GREEN":
            assert all(signal == "RED" for signal in view["signals"].values())
    assert calculate_metrics(session.simulation.result()) == calculate_metrics(headless)
    assert session.simulation.vehicles == headless.vehicles
    assert session.simulation.switches == headless.switches


@pytest.mark.parametrize("times", [(5, 4), (-1,), (80,), (float("nan"),)])
def test_invalid_pedestrian_schedule(times):
    config = Config(simulation_duration=80)
    with pytest.raises(ValueError):
        Simulation(config, TrafficPlan("EMPTY", 0, 80, ()), FixedController(config), times)


@pytest.mark.parametrize("controller_name", ["ADAPTIVE", "QUEUE_ONLY"])
def test_timeout_can_return_to_same_busy_phase_after_safe_pedestrian_cycle(controller_name):
    config = Config(simulation_duration=80, max_green=120)
    arrivals = tuple(Arrival(index, Direction.NORTH, 0) for index in range(100)) + (Arrival(101, Direction.EAST, 0),)
    simulation = Simulation(config, TrafficPlan("BUSY", 0, 80, arrivals), CONTROLLERS[controller_name](config), (0,))
    while simulation.time < 59:
        simulation.step()
    assert simulation.switches[0]["reason"] == "PED_MAX_WAIT"
    assert simulation.switches[0]["new_phase"] == "NS"
    assert simulation.light.state == State.NS_GREEN
    assert simulation.light.state_started_at == 58
    assert not simulation.light.pedestrian_request
    assert not any(v.crossing_time is not None and 40 < v.crossing_time < 60 for v in simulation.vehicles)


def test_new_request_after_completed_cycle_is_served_separately():
    config = Config(simulation_duration=120)
    simulation = Simulation(config, TrafficPlan("EMPTY", 0, 120, ()), AdaptiveController(config), (0, 5, 70))
    starts = []
    previous = simulation.light.state
    while not simulation.finished:
        simulation.step()
        if simulation.light.state == State.PED_GREEN and previous != State.PED_GREEN:
            starts.append(simulation.light.state_started_at)
        previous = simulation.light.state
    assert starts == [44, 114]
    assert simulation.result().completed_successfully
