"""CLI для одиночных и парных экспериментов без графического интерфейса."""

import argparse
import csv
from dataclasses import asdict, fields
from datetime import datetime
import json
from pathlib import Path
import platform

from .config import Config, SCENARIO_PROFILES
from .controllers import CONTROLLERS
from .metrics import DIAGNOSTIC_FIELDS, RESULT_FIELDS, calculate_diagnostics, calculate_metrics
from .scenarios import create_traffic_plan
from .simulation import Simulation, SimulationResult
from .traffic_demand import TrafficPlan


SWITCH_FIELDS = (
    "simulation_time", "old_phase", "new_phase", "reason",
    "queue_NS", "queue_EW", "wait_NS", "wait_EW", "priority_NS", "priority_EW",
)


def run_comparison(
    config: Config, plan: TrafficPlan, controller_names: tuple[str, ...] = ("FIXED", "ADAPTIVE"),
    pedestrian_times: tuple[float, ...] = (),
) -> tuple[SimulationResult, ...]:
    # Именно один объект plan, а не две повторные генерации с тем же seed.
    return tuple(Simulation(config, plan, CONTROLLERS[name](config), pedestrian_times).run() for name in controller_names)


def run_experiments(
    config: Config, scenarios: list[str], seeds: list[int], output: Path,
    controller_name: str = "both",
    pedestrian_times: tuple[float, ...] = (),
) -> Path:
    if controller_name not in ("both", "all", "fixed", "queue_only", "adaptive"):
        raise ValueError("Unknown controller")
    if controller_name == "both":
        controller_names = ("FIXED", "ADAPTIVE")
    elif controller_name == "all":
        controller_names = tuple(CONTROLLERS)
    else:
        controller_names = (controller_name.upper(),)
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
        "pedestrian_times": pedestrian_times,
        "python_version": platform.python_version(),
        "created_at": datetime.now().astimezone().isoformat(),
        "total_clearing_time_definition": "Time from t=0 including generation and drain, seconds",
        "mean_queue_definition": "Total queue sampled before service over the entire run",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    csv_path = output / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream, (output / "diagnostics.csv").open("w", newline="", encoding="utf-8") as diagnostics_stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        diagnostics_writer = csv.DictWriter(diagnostics_stream, fieldnames=DIAGNOSTIC_FIELDS)
        diagnostics_writer.writeheader()
        for scenario in scenarios:
            for seed in seeds:
                plan = create_traffic_plan(scenario, seed, config.simulation_duration)
                results = run_comparison(config, plan, controller_names, pedestrian_times)
                for result in results:
                    metrics = calculate_metrics(result)
                    writer.writerow(metrics)
                    diagnostics_writer.writerow(calculate_diagnostics(result))
                    stream.flush()
                    diagnostics_stream.flush()
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
    parser.add_argument("--controller", type=str.lower, choices=("both", "all", "fixed", "queue_only", "adaptive"), default="both")
    parser.add_argument("--scenario", type=str.upper, choices=("ALL", *SCENARIO_PROFILES), default="NS_HEAVY")
    parser.add_argument("--runs", type=int, default=1, help="Number of consecutive seeds")
    parser.add_argument("--seed", type=int, default=42, help="First seed")
    parser.add_argument("--output", type=Path, help="New output directory (must not exist)")
    parser.add_argument("--visualize", action="store_true", help="Open the pygame demonstration")
    parser.add_argument("--pedestrian-times", type=float, nargs="*", default=[], metavar="SECONDS", help="Optional sorted request times, identical for every controller")
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
    pedestrian_times = tuple(args.pedestrian_times)
    if any(not 0 <= time < config.simulation_duration for time in pedestrian_times) or sorted(pedestrian_times) != list(pedestrian_times):
        parser.error("--pedestrian-times must be sorted and lie in [0, duration)")
    if args.visualize:
        if args.controller in ("both", "all") or args.scenario == "ALL" or args.runs != 1:
            parser.error("GUI requires one --controller (fixed/queue_only/adaptive), one scenario and --runs 1")
        if args.output is not None:
            parser.error("--output is for headless experiments; GUI does not write experiment CSV")
        from .gui import run_gui
        from .presentation import GuiSession
        plan = create_traffic_plan(args.scenario, args.seed, config.simulation_duration)
        return run_gui(GuiSession(config, plan, args.controller, pedestrian_times))
    output = args.output or Path("results") / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    scenarios = list(SCENARIO_PROFILES) if args.scenario == "ALL" else [args.scenario]
    try:
        csv_path = run_experiments(config, scenarios, list(range(args.seed, args.seed + args.runs)), output, args.controller, pedestrian_times)
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
