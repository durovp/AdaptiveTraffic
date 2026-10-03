import csv

import pytest

from adaptive_traffic.analyze import compare_all_pairs, load_results, main, relative_improvement, select_complete_pairs, summarize
from adaptive_traffic.config import Config
from adaptive_traffic.experiments import run_experiments
from adaptive_traffic.metrics import METRIC_NAMES, RESULT_FIELDS


def row(controller, seed=42, value=10, completed=True):
    result = {metric: float(value) for metric in METRIC_NAMES}
    result.update(scenario="TEST", controller=controller, seed=seed, completed_successfully=completed)
    result["vehicles_generated"] = result["vehicles_passed"] = 10.0
    result["active_generation_duration"] = result["total_clearing_time"] = 60.0
    return result


def test_improvement_sign_and_zero_baseline():
    assert relative_improvement(10, 8) == 20
    assert relative_improvement(10, 12) == -20
    assert relative_improvement(10, 12, higher_is_better=True) == 20
    assert relative_improvement(0, 0) is None
    assert relative_improvement(0, 8) is None


def test_excludes_whole_incomplete_and_unmatched_pairs():
    rows = [row("FIXED"), row("ADAPTIVE"), row("FIXED", 43), row("ADAPTIVE", 43, completed=False), row("FIXED", 44)]
    selected, exclusions = select_complete_pairs(rows)
    assert len(selected) == 2
    assert {r["seed"] for r in selected} == {42}
    assert len(exclusions) == 2


def test_paired_demand_mismatch_is_rejected():
    fixed, adaptive = row("FIXED"), row("ADAPTIVE")
    adaptive["vehicles_generated"] = 11
    with pytest.raises(ValueError, match="vehicles_generated"):
        select_complete_pairs([fixed, adaptive])


def test_summary_means_sample_sd_and_paired_differences():
    rows = [row("FIXED", 42, 10), row("ADAPTIVE", 42, 8), row("FIXED", 43, 20), row("ADAPTIVE", 43, 12)]
    summary, comparisons = summarize(rows)
    fixed = next(r for r in summary if r["controller"] == "FIXED" and r["metric"] == "mean_wait")
    assert fixed["mean"] == 15
    assert fixed["std"] == pytest.approx(50 ** 0.5)
    comparison = next(r for r in comparisons if r["metric"] == "mean_wait")
    assert comparison["adaptive_mean"] == 10
    assert comparison["fixed_minus_adaptive_mean"] == 5
    assert comparison["fixed_minus_adaptive_std"] == pytest.approx(18 ** 0.5)
    assert comparison["improvement_percent"] == pytest.approx(100 / 3)
    switches = next(r for r in comparisons if r["metric"] == "number_of_switches")
    assert switches["improvement_percent"] is None


def test_single_seed_has_undefined_sample_sd():
    summary, comparisons = summarize([row("FIXED"), row("ADAPTIVE")])
    assert all(r["std"] is None for r in summary)
    assert all(r["fixed_minus_adaptive_std"] is None for r in comparisons)


@pytest.mark.parametrize("problem", ["duplicate", "nan", "false_success"])
def test_rejects_invalid_csv(tmp_path, problem):
    records = [row("FIXED")]
    if problem == "duplicate":
        records.append(row("FIXED"))
    elif problem == "nan":
        records[0]["mean_wait"] = float("nan")
    else:
        records[0]["vehicles_passed"] = 0
    path = tmp_path / "bad.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(records)
    with pytest.raises(ValueError):
        load_results(path)


def test_real_csv_analysis(tmp_path):
    path = run_experiments(Config(simulation_duration=60), ["NS_HEAVY"], [42, 43], tmp_path / "experiment")
    assert main([str(path), "--no-plots"]) == 0
    output = path.parent / "analysis"
    assert (output / "summary.csv").exists()
    assert (output / "comparison.csv").exists()
    assert "Complete pairs used: 2" in (output / "analysis_report.txt").read_text()


def test_single_controller_is_not_presented_as_comparison(tmp_path):
    path = run_experiments(Config(simulation_duration=60), ["NS_HEAVY"], [42], tmp_path / "single", "fixed")
    assert main([str(path), "--no-plots"]) == 1
    assert "missing controller" in (path.parent / "analysis" / "analysis_report.txt").read_text()


def test_complete_triples_and_queue_only_comparison():
    rows = [row("FIXED", value=20), row("QUEUE_ONLY", value=10), row("ADAPTIVE", value=8),
            row("FIXED", 43), row("ADAPTIVE", 43)]
    selected, excluded = select_complete_pairs(rows)
    assert len(selected) == 3 and len(excluded) == 1
    summary, legacy = summarize(selected)
    assert {record["controller"] for record in summary} == {"FIXED", "QUEUE_ONLY", "ADAPTIVE"}
    assert next(record for record in legacy if record["metric"] == "mean_wait")["fixed_mean"] == 20
    comparisons = compare_all_pairs(selected)
    comparison = next(record for record in comparisons if record["baseline"] == "QUEUE_ONLY" and record["metric"] == "mean_wait")
    assert comparison["controller"] == "ADAPTIVE"
    assert comparison["improvement_percent"] == 20


def test_incomplete_queue_only_excludes_whole_triple():
    rows = [row("FIXED"), row("QUEUE_ONLY", completed=False), row("ADAPTIVE")]
    selected, exclusions = select_complete_pairs(rows)
    assert selected == [] and len(exclusions) == 1


def test_three_controller_csv_analysis(tmp_path):
    path = run_experiments(Config(simulation_duration=60), ["NS_HEAVY"], [42, 43], tmp_path / "three", "all")
    assert main([str(path), "--no-plots"]) == 0
    assert "Complete groups used: 2" in (path.parent / "analysis" / "analysis_report.txt").read_text()
    with (path.parent / "analysis" / "summary.csv").open() as stream:
        assert {record["controller"] for record in csv.DictReader(stream)} == {"FIXED", "QUEUE_ONLY", "ADAPTIVE"}
