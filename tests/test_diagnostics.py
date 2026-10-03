import csv

import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import AdaptiveController, FixedController
from adaptive_traffic.experiments import run_experiments
from adaptive_traffic.metrics import calculate_diagnostics
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.traffic_demand import TrafficPlan


def empty_run(duration, controller=FixedController, pedestrian_times=()):
    config = Config(simulation_duration=duration)
    return Simulation(config, TrafficPlan("EMPTY", 0, duration, ()), controller(config), pedestrian_times).run()


def test_fixed_cycle_diagnostics_are_hand_calculable():
    result = empty_run(68)
    diagnostics = calculate_diagnostics(result)
    assert diagnostics["switches_FIXED_TIMER"] == 2
    assert diagnostics["average_green_duration"] == 30
    assert diagnostics["total_yellow_time"] == 6
    assert diagnostics["total_all_red_time"] == 2
    assert diagnostics["transition_time_fraction"] == pytest.approx(8 / 68)
    assert sum(result.state_durations.values()) == result.elapsed_time


def test_partial_last_green_is_included_and_diagnostics_do_not_mutate_result():
    result = empty_run(35)
    diagnostics = calculate_diagnostics(result)
    assert result.green_durations == [30, 1]
    assert diagnostics["average_green_duration"] == 15.5
    assert calculate_diagnostics(result) == diagnostics


def test_empty_adaptive_has_one_continuous_green_and_no_transitions():
    diagnostics = calculate_diagnostics(empty_run(68, AdaptiveController))
    assert diagnostics["average_green_duration"] == 68
    assert diagnostics["transition_time_fraction"] == 0
    assert all(value == 0 for key, value in diagnostics.items() if key.startswith("switches_"))


def test_pedestrian_clearance_is_not_counted_as_all_red():
    result = empty_run(60, pedestrian_times=(5,))
    diagnostics = calculate_diagnostics(result)
    assert result.state_durations["PED_GREEN"] == 10
    assert result.state_durations["PED_CLEARANCE"] == 3
    assert diagnostics["total_yellow_time"] == 3
    assert diagnostics["total_all_red_time"] == 2
    assert diagnostics["average_green_duration"] == 21  # NS 30 + EW 12.
    assert diagnostics["transition_time_fraction"] == pytest.approx(5 / 60)
    assert sum(result.state_durations.values()) == 60


def test_diagnostics_csv_has_one_row_per_run_and_accounts_for_switches(tmp_path):
    path = run_experiments(Config(simulation_duration=120), ["NS_HEAVY"], [42, 43], tmp_path / "diagnostics", "all")
    with path.open() as stream:
        results = list(csv.DictReader(stream))
    with (path.parent / "diagnostics.csv").open() as stream:
        diagnostics = list(csv.DictReader(stream))
    assert len(diagnostics) == len(results) == 6
    for result, diagnostic in zip(results, diagnostics):
        assert (result["scenario"], result["controller"], result["seed"]) == (diagnostic["scenario"], diagnostic["controller"], diagnostic["seed"])
        assert sum(int(value) for key, value in diagnostic.items() if key.startswith("switches_")) == int(result["number_of_switches"])
        assert 0 <= float(diagnostic["transition_time_fraction"]) <= 1
        assert diagnostic["completed_successfully"] == "True"
