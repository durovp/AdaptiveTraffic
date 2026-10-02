"""CLI для одиночных и парных экспериментов без графического интерфейса."""

import argparse
import csv
from dataclasses import asdict, fields
from datetime import datetime
import json
from pathlib import Path
import platform

from .config import Config, SCENARIO_PROFILES
from .controllers import AdaptiveController, FixedController
from .metrics import RESULT_FIELDS, calculate_metrics
from .scenarios import create_traffic_plan
from .simulation import Simulation, SimulationResult
from .traffic_demand import TrafficPlan


SWITCH_FIELDS = (
    "simulation_time", "old_phase", "new_phase", "reason",
    "queue_NS", "queue_EW", "wait_NS", "wait_EW", "priority_NS", "priority_EW",
)


def run_comparison(config: Config, plan: TrafficPlan) -> tuple[SimulationResult, SimulationResult]:
    # Именно один объект plan, а не две повторные генерации с тем же seed.
    fixed = Simulation(config, plan, FixedController(config)).run()
    adaptive = Simulation(config, plan, AdaptiveController(config)).run()
    return fixed, adaptive


def run_experiments(
    config: Config, scenarios: list[str], seeds: list[int], output: Path,
    controller_name: str = "both",
) -> Path:
    if controller_name not in ("both", "fixed", "adaptive"):
        raise ValueError("Unknown controller")
    if not scenarios or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Provide scenarios and distinct seeds")
    scenarios = [scenario.upper() for scenario in scenarios]
    if len(set(scenarios)) != len(scenarios) or any(s not in SCENARIO_PROFILES for s in scenarios):
        raise ValueError("Provide distinct known scenarios")

    # Не перезаписываем результаты предыдущего исследования.
    output.mkdir(parents=True, exist_ok=False)
    logs = output / "switches"
    logs.mkdir()
    metadata = {
        "config": asdict(config),
        "scenario_profiles": {name: SCENARIO_PROFILES[name] for name in scenarios},
        "seeds": seeds,
        "controller": controller_name,
        "python_version": platform.python_version(),
        "created_at": datetime.now().astimezone().isoformat(),
        "total_clearing_time_definition": "Time from t=0 including generation and drain, seconds",
        "mean_queue_definition": "Total queue sampled before service over the entire run",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    csv_path = output / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for scenario in scenarios:
            for seed in seeds:
                plan = create_traffic_plan(scenario, seed, config.simulation_duration)
                if controller_name == "both":
                    results = run_comparison(config, plan)
                else:
                    controller = FixedController(config) if controller_name == "fixed" else AdaptiveController(config)
                    results = (Simulation(config, plan, controller).run(),)
                for result in results:
                    metrics = calculate_metrics(result)
                    writer.writerow(metrics)
                    stream.flush()
                    log_path = logs / f"{scenario}_{seed}_{result.controller}.csv"
                    with log_path.open("w", newline="", encoding="utf-8") as log_stream:
                        log_writer = csv.DictWriter(log_stream, fieldnames=SWITCH_FIELDS)
                        log_writer.writeheader()
                        log_writer.writerows(result.switches)
                    status = "OK" if result.completed_successfully else "INCOMPLETE"
                    print(
                        f"{scenario} seed={seed} {result.controller}: {status}; "
                        f"passed={metrics['vehicles_passed']}/{metrics['vehicles_generated']}; "
                        f"mean_wait={metrics['mean_wait']:.2f} s"
                    )
    return csv_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AdaptiveTraffic: simulation and paired experiments")
    parser.add_argument("--controller", type=str.lower, choices=("both", "fixed", "adaptive"), default="both")
    parser.add_argument("--scenario", type=str.upper, choices=("ALL", *SCENARIO_PROFILES), default="NS_HEAVY")
    parser.add_argument("--runs", type=int, default=1, help="Number of consecutive seeds")
    parser.add_argument("--seed", type=int, default=42, help="First seed")
    parser.add_argument("--output", type=Path, help="New output directory (must not exist)")
    for field in fields(Config):
        option = "--duration" if field.name == "simulation_duration" else "--" + field.name.replace("_", "-")
        parser.add_argument(option, dest=field.name, type=float, default=field.default)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be positive")
    try:
        config = Config(**{field.name: getattr(args, field.name) for field in fields(Config)})
    except ValueError as error:
        parser.error(str(error))
    output = args.output or Path("results") / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    scenarios = list(SCENARIO_PROFILES) if args.scenario == "ALL" else [args.scenario]
    try:
        csv_path = run_experiments(config, scenarios, list(range(args.seed, args.seed + args.runs)), output, args.controller)
    except FileExistsError:
        parser.error(f"Output directory already exists: {output}; choose a new directory")
    print(f"Results: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as stream:
        if any(row["completed_successfully"] != "True" for row in csv.DictReader(stream)):
            print("INCOMPLETE runs are present; do not interpret them as successful experiments.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
