from dataclasses import FrozenInstanceError

import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import Action, FixedController, Reason
from adaptive_traffic.models import Direction, Phase, PhaseStats, TrafficSnapshot, Vehicle
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.traffic_demand import Arrival, DemandSegment, TrafficPlan, generate_traffic_plan
from adaptive_traffic.traffic_light import State, TrafficLight


def make_simulation(arrivals=(), **config_values):
    config = Config(simulation_duration=80, **config_values)
    plan = TrafficPlan("TEST", 42, config.simulation_duration, tuple(arrivals))
    return Simulation(config, plan, FixedController(config))


def test_safe_cycle_in_both_directions():
    light = TrafficLight(Config())
    assert light.state == State.NS_GREEN
    assert light.request_switch(30)
    assert light.state == State.NS_YELLOW
    assert not light.request_switch(31)
    light.advance(32)
    assert light.state == State.NS_YELLOW
    light.advance(33)
    assert light.state == State.ALL_RED_TO_EW
    light.advance(34)
    assert light.state == State.EW_GREEN
    assert light.request_switch(64)
    assert light.state == State.EW_YELLOW
    light.advance(66)
    assert light.state == State.EW_YELLOW
    light.advance(67)
    assert light.state == State.ALL_RED_TO_NS
    light.advance(68)
    assert light.state == State.NS_GREEN


def test_never_conflicting_greens_and_fixed_timing():
    simulation = make_simulation()
    states = {}
    for _ in range(80):
        time = simulation.time
        simulation.step()
        states[time] = simulation.light.state
        directions = set(simulation.light.allowed_directions)
        assert not (directions & {Direction.NORTH, Direction.SOUTH}
                    and directions & {Direction.EAST, Direction.WEST})
    assert all(states[t] == State.NS_GREEN for t in range(30))
    assert all(states[t] == State.NS_YELLOW for t in range(30, 33))
    assert states[33] == State.ALL_RED_TO_EW
    assert all(states[t] == State.EW_GREEN for t in range(34, 64))
    assert states[67] == State.ALL_RED_TO_NS
    assert states[68] == State.NS_GREEN


@pytest.mark.parametrize("green_time, action", [(0, Action.KEEP), (29, Action.KEEP), (30, Action.REQUEST_SWITCH)])
def test_fixed_ignores_queues(green_time, action):
    controller = FixedController(Config())
    empty = TrafficSnapshot(PhaseStats(), PhaseStats())
    busy = TrafficSnapshot(PhaseStats(100, 200), PhaseStats(500, 600))
    assert controller.decide(Phase.NS, green_time, empty) == controller.decide(Phase.NS, green_time, busy)
    assert controller.decide(Phase.EW, green_time, busy).action == action
    if action == Action.REQUEST_SWITCH:
        assert controller.decide(Phase.NS, green_time, empty).reason == Reason.FIXED_TIMER


def test_headway_independent_directions_and_red():
    arrivals = (
        Arrival(1, Direction.NORTH, 0), Arrival(2, Direction.NORTH, 0),
        Arrival(3, Direction.SOUTH, 0), Arrival(4, Direction.EAST, 0),
    )
    simulation = make_simulation(arrivals)
    simulation.step()
    assert all(vehicle.crossing_time is None for vehicle in simulation.vehicles)
    simulation.step()
    assert [v.crossing_time for v in simulation.vehicles] == [2, None, 2, None]
    simulation.step()
    simulation.step()
    assert simulation.vehicles[1].crossing_time == 4
    while simulation.time < 35:
        simulation.step()
    assert simulation.vehicles[3].crossing_time is None
    simulation.step()
    assert simulation.vehicles[3].crossing_time == 36


def test_no_passage_during_yellow_or_all_red():
    arrivals = tuple(Arrival(i, Direction.NORTH, 0) for i in range(30))
    simulation = make_simulation(arrivals)
    simulation.run()
    times = [v.crossing_time for v in simulation.vehicles]
    # t=30 завершает последний зелёный интервал [29,30).
    assert 30 in times
    assert not any(30 < time <= 68 for time in times)


def test_waiting_time_and_fractional_arrival():
    simulation = make_simulation((Arrival(1, Direction.NORTH, 0.25),))
    result = simulation.run()
    assert result.vehicles[0].waiting_time == 1.75
    assert Vehicle(2, Direction.EAST, 1).waiting_time is None


def test_snapshot_uses_sum_and_oldest_in_each_phase():
    simulation = make_simulation()
    simulation.step_index = 10
    simulation.queues[Direction.NORTH].append(Vehicle(1, Direction.NORTH, 3))
    simulation.queues[Direction.SOUTH].append(Vehicle(2, Direction.SOUTH, 5))
    assert simulation.snapshot() == TrafficSnapshot(PhaseStats(2, 7), PhaseStats())


def test_reproducible_immutable_plan():
    segments = (DemandSegment(0, 80, (20, 20, 20, 20)),)
    first = generate_traffic_plan("TEST", 42, 80, segments)
    assert first == generate_traffic_plan("TEST", 42, 80, segments)
    assert first != generate_traffic_plan("TEST", 43, 80, segments)
    with pytest.raises(FrozenInstanceError):
        first.arrivals[0].arrival_time = 0
    config = Config(simulation_duration=80)
    Simulation(config, first, FixedController(config)).run()
    assert first == generate_traffic_plan("TEST", 42, 80, segments)


def test_empty_plan_completes_at_generation_end():
    result = make_simulation().run()
    assert result.completed_successfully
    assert result.elapsed_time == 80
    assert result.vehicles == []


def test_drain_serves_last_fractional_arrival():
    result = make_simulation((Arrival(1, Direction.EAST, 79.5),)).run()
    assert result.completed_successfully
    assert result.elapsed_time > 80
    assert result.vehicles[0].crossing_time > 80


def test_timeout_reports_incomplete_and_keeps_unpassed_vehicle():
    config = Config(simulation_duration=1, drain_timeout=1)
    plan = TrafficPlan("TEST", 0, 1, (Arrival(1, Direction.EAST, 0),))
    result = Simulation(config, plan, FixedController(config)).run()
    assert not result.completed_successfully
    assert result.elapsed_time == 2
    assert result.vehicles[0].crossing_time is None


@pytest.mark.parametrize("values", [
    {"time_step": 0}, {"yellow": 0}, {"vehicle_headway": 0.5},
    {"wait_equivalent": 0}, {"switch_margin": -1},
    {"min_green": 50}, {"max_wait": float("nan")},
])
def test_invalid_config_is_rejected(values):
    with pytest.raises(ValueError):
        Config(**values)
