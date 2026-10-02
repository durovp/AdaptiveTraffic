import csv
import json

import pytest

from adaptive_traffic.config import Config, SCENARIO_PROFILES
from adaptive_traffic.controllers import FixedController
from adaptive_traffic.experiments import main, run_comparison, run_experiments
from adaptive_traffic.metrics import calculate_metrics, percentile
from adaptive_traffic.models import Direction
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.traffic_demand import Arrival, TrafficPlan


@pytest.mark.parametrize("values,percent,expected", [
    ([], 95, 0), ([12], 95, 12), ([0, 10, 20, 30, 40], 95, 38),
    ([10, 0], 95, 9.5), ([3, 1, 2], 0, 1), ([3, 1, 2], 100, 3),
])
def test_percentile(values, percent, expected):
    assert percentile(values, percent) == pytest.approx(expected)


def test_metrics_hand_calculated():
    config = Config(simulation_duration=4)
    plan = TrafficPlan("TEST", 0, 4, (Arrival(1, Direction.NORTH, 0), Arrival(2, Direction.NORTH, 0)))
    metrics = calculate_metrics(Simulation(config, plan, FixedController(config)).run())
    assert metrics["mean_wait"] == metrics["median_wait"] == 3
    assert metrics["p95_wait"] == pytest.approx(3.9)
    assert metrics["max_wait"] == 4
    assert metrics["mean_queue"] == 1.5  # Сэмплы: 2, 2, 1, 1.
    assert metrics["max_queue"] == 2
    assert metrics["vehicles_generated"] == metrics["vehicles_passed"] == 2
    assert metrics["active_generation_duration"] == metrics["total_clearing_time"] == 4
    assert metrics["number_of_switches"] == 0


def test_empty_metrics_are_zero_except_duration_and_fixed_switches():
    config = Config(simulation_duration=80)
    result = Simulation(config, TrafficPlan("EMPTY", 0, 80, ()), FixedController(config)).run()
    metrics = calculate_metrics(result)
    for field in ("mean_wait", "median_wait", "p95_wait", "max_wait", "mean_queue", "max_queue", "vehicles_generated", "vehicles_passed"):
        assert metrics[field] == 0


def test_pair_shares_exact_plan_but_not_mutable_vehicles():
    config = Config(simulation_duration=60)
    plan = create_traffic_plan("NS_HEAVY", 42, 60)
    original = plan.arrivals
    fixed, adaptive = run_comparison(config, plan)
    assert fixed.plan is plan and adaptive.plan is plan
    assert plan.arrivals is original
    assert all(a is not b for a, b in zip(fixed.vehicles, adaptive.vehicles))
    assert [(v.id, v.direction, v.arrival_time) for v in fixed.vehicles] == [
        (v.id, v.direction, v.arrival_time) for v in adaptive.vehicles
    ]
    assert fixed.completed_successfully and adaptive.completed_successfully


@pytest.mark.parametrize("scenario", SCENARIO_PROFILES)
def test_every_scenario_is_reproducible_and_clears(scenario):
    config = Config(simulation_duration=120)
    plan = create_traffic_plan(scenario, 12345, 120)
    assert plan == create_traffic_plan(scenario, 12345, 120)
    for result in run_comparison(config, plan):
        assert result.completed_successfully
        assert all(v.crossing_time is not None for v in result.vehicles)


def test_experiment_writes_real_pairs_metadata_and_decisions(tmp_path):
    output = tmp_path / "experiment"
    path = run_experiments(Config(simulation_duration=80), ["NS_HEAVY"], [42, 43], output)
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert rows[0]["vehicles_generated"] == rows[1]["vehicles_generated"]
    assert all(row["completed_successfully"] == "True" for row in rows)
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["config"]["wait_equivalent"] == 10
    assert metadata["seeds"] == [42, 43]
    with (output / "switches" / "NS_HEAVY_42_ADAPTIVE.csv").open() as stream:
        switches = list(csv.DictReader(stream))
    assert switches
    assert all("priority_NS" in row and "priority_EW" in row for row in switches)
    with pytest.raises(FileExistsError):
        run_experiments(Config(), ["NS_HEAVY"], [42], output)


def test_cli_reports_incomplete_with_nonzero_exit(tmp_path):
    exit_code = main([
        "--scenario", "balanced_high", "--duration", "60", "--drain-timeout", "1",
        "--output", str(tmp_path / "incomplete"),
    ])
    assert exit_code == 1
    with (tmp_path / "incomplete" / "results.csv").open() as stream:
        assert any(row["completed_successfully"] == "False" for row in csv.DictReader(stream))
