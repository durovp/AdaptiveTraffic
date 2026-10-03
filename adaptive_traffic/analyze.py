"""Анализ реального CSV: полные пары seeds, средние, разброс и графики."""

import argparse
from collections import defaultdict
import csv
from itertools import combinations
import math
import os
from pathlib import Path
import statistics

from .metrics import METRIC_NAMES, RESULT_FIELDS
from .controllers import CONTROLLERS


LOWER_IS_BETTER = {
    "mean_wait", "median_wait", "p95_wait", "max_wait",
    "mean_queue", "max_queue", "total_clearing_time",
}
HIGHER_IS_BETTER = {"vehicles_passed"}
PLOTS = {
    "mean_wait": ("Mean waiting time", "Seconds"),
    "p95_wait": ("95th percentile of waiting time", "Seconds"),
    "mean_queue": ("Mean total queue", "Vehicles"),
    "total_clearing_time": ("Total time: generation + drain", "Seconds"),
}


def relative_improvement(fixed: float, adaptive: float, higher_is_better=False) -> float | None:
    # При нулевой базе процент не определён, даже если оба результата равны 0.
    if fixed == 0:
        return None
    change = adaptive - fixed if higher_is_better else fixed - adaptive
    return change / fixed * 100


def load_results(path: Path) -> list[dict]:
    rows = []
    seen = set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        missing = set(RESULT_FIELDS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing CSV fields: {', '.join(sorted(missing))}")
        for line, row in enumerate(reader, start=2):
            if row["controller"] not in CONTROLLERS:
                raise ValueError(f"Line {line}: unknown controller")
            row["seed"] = int(row["seed"])
            if row["completed_successfully"] not in ("True", "False"):
                raise ValueError(f"Line {line}: invalid completion flag")
            row["completed_successfully"] = row["completed_successfully"] == "True"
            for metric in METRIC_NAMES:
                row[metric] = float(row[metric])
                if not math.isfinite(row[metric]) or row[metric] < 0:
                    raise ValueError(f"Line {line}: invalid {metric}")
            for metric in ("vehicles_generated", "vehicles_passed", "max_queue", "number_of_switches"):
                if not row[metric].is_integer():
                    raise ValueError(f"Line {line}: {metric} must be an integer")
            if row["vehicles_passed"] > row["vehicles_generated"]:
                raise ValueError(f"Line {line}: passed exceeds generated")
            if row["completed_successfully"] and row["vehicles_passed"] != row["vehicles_generated"]:
                raise ValueError(f"Line {line}: successful run contains unpassed vehicles")
            if row["active_generation_duration"] <= 0 or row["total_clearing_time"] < row["active_generation_duration"]:
                raise ValueError(f"Line {line}: invalid experiment duration")
            key = (row["scenario"], row["seed"], row["controller"])
            if key in seen:
                raise ValueError(f"Duplicate experiment {key}; do not mix parameter studies in one CSV")
            seen.add(key)
            rows.append(row)
    return rows


def select_complete_pairs(rows: list[dict]) -> tuple[list[dict], list[str]]:
    # Старые CSV сравниваются попарно. Если есть QUEUE_ONLY, для каждого seed
    # нужна полная тройка, чтобы столбцы графика опирались на одинаковые seeds.
    controllers = tuple(CONTROLLERS) if any(row["controller"] == "QUEUE_ONLY" for row in rows) else ("FIXED", "ADAPTIVE")
    pairs = defaultdict(dict)
    for row in rows:
        pairs[(row["scenario"], row["seed"])][row["controller"]] = row
    selected = []
    exclusions = []
    for (scenario, seed), pair in sorted(pairs.items()):
        if set(pair) != set(controllers):
            exclusions.append(f"{scenario} seed={seed}: missing controller; pair excluded")
            continue
        if not all(row["completed_successfully"] for row in pair.values()):
            exclusions.append(f"{scenario} seed={seed}: incomplete run; ALL compared controllers excluded")
            continue
        for field in ("vehicles_generated", "active_generation_duration"):
            if len({row[field] for row in pair.values()}) != 1:
                raise ValueError(f"{scenario} seed={seed}: paired {field} differs")
        selected.extend(pair[name] for name in controllers)
    return selected, exclusions


def summarize(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Каждый seed имеет одинаковый вес; машины разных seeds не объединяются."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["controller"])].append(row)
    summary = []
    comparisons = []
    for scenario in sorted({row["scenario"] for row in rows}):
        fixed_rows = sorted(groups[(scenario, "FIXED")], key=lambda row: row["seed"])
        adaptive_rows = sorted(groups[(scenario, "ADAPTIVE")], key=lambda row: row["seed"])
        if [row["seed"] for row in fixed_rows] != [row["seed"] for row in adaptive_rows]:
            raise ValueError("Summaries require matching seeds")
        for metric in METRIC_NAMES:
            fixed_values = [row[metric] for row in fixed_rows]
            adaptive_values = [row[metric] for row in adaptive_rows]
            fixed_mean = statistics.mean(fixed_values)
            adaptive_mean = statistics.mean(adaptive_values)
            for controller, values in (("FIXED", fixed_values), ("ADAPTIVE", adaptive_values)):
                summary.append({
                    "scenario": scenario, "controller": controller, "metric": metric,
                    "seeds": len(values), "mean": statistics.mean(values),
                    # Выборочное стандартное отклонение не определено при n=1.
                    "std": statistics.stdev(values) if len(values) > 1 else None,
                })
            differences = [fixed - adaptive for fixed, adaptive in zip(fixed_values, adaptive_values)]
            improvement = None
            if metric in LOWER_IS_BETTER | HIGHER_IS_BETTER:
                improvement = relative_improvement(fixed_mean, adaptive_mean, metric in HIGHER_IS_BETTER)
            comparisons.append({
                "scenario": scenario, "metric": metric, "seeds": len(fixed_rows),
                "fixed_mean": fixed_mean, "adaptive_mean": adaptive_mean,
                "fixed_minus_adaptive_mean": statistics.mean(differences),
                "fixed_minus_adaptive_std": statistics.stdev(differences) if len(differences) > 1 else None,
                "improvement_percent": improvement,
            })
    # Формат comparison.csv для FIXED/ADAPTIVE остаётся прежним.
    # Третий алгоритм добавляется в summary и в отдельное общее парное сравнение.
    for (scenario, controller), records in sorted(groups.items()):
        if controller == "QUEUE_ONLY":
            for metric in METRIC_NAMES:
                values = [row[metric] for row in records]
                summary.append({
                    "scenario": scenario, "controller": controller, "metric": metric,
                    "seeds": len(values), "mean": statistics.mean(values),
                    "std": statistics.stdev(values) if len(values) > 1 else None,
                })
    return summary, comparisons


def compare_all_pairs(rows: list[dict]) -> list[dict]:
    """В том числе QUEUE_ONLY vs ADAPTIVE: измеряем добавочную пользу ожидания."""
    groups = defaultdict(dict)
    for row in rows:
        groups[(row["scenario"], row["controller"])][row["seed"]] = row
    comparisons = []
    for scenario in sorted({row["scenario"] for row in rows}):
        controllers = [name for name in CONTROLLERS if (scenario, name) in groups]
        for baseline, target in combinations(controllers, 2):
            baseline_rows = groups[(scenario, baseline)]
            target_rows = groups[(scenario, target)]
            if baseline_rows.keys() != target_rows.keys():
                raise ValueError("Pairwise comparison requires matching seeds")
            seeds = sorted(baseline_rows)
            for metric in METRIC_NAMES:
                base = [baseline_rows[seed][metric] for seed in seeds]
                values = [target_rows[seed][metric] for seed in seeds]
                differences = [a - b for a, b in zip(base, values)]
                improvement = None
                if metric in LOWER_IS_BETTER | HIGHER_IS_BETTER:
                    improvement = relative_improvement(statistics.mean(base), statistics.mean(values), metric in HIGHER_IS_BETTER)
                comparisons.append({
                    "scenario": scenario, "baseline": baseline, "controller": target,
                    "metric": metric, "seeds": len(seeds),
                    "baseline_mean": statistics.mean(base), "controller_mean": statistics.mean(values),
                    "baseline_minus_controller_mean": statistics.mean(differences),
                    "baseline_minus_controller_std": statistics.stdev(differences) if len(seeds) > 1 else None,
                    "improvement_percent": improvement,
                })
    return comparisons


def write_table(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def create_plots(summary: list[dict], output: Path):
    # В ограниченной среде домашняя папка может быть недоступна для записи.
    # Кеш шрифтов относится к графикам этого проекта, поэтому храним его рядом.
    os.environ.setdefault("MPLCONFIGDIR", str((output / ".matplotlib").resolve()))
    import matplotlib

    matplotlib.use("Agg")  # Сохраняем файлы, не открывая окна.
    import matplotlib.pyplot as plt

    scenarios = sorted({row["scenario"] for row in summary})
    controllers = [name for name in CONTROLLERS if any(row["controller"] == name for row in summary)]
    colors = {"FIXED": "#4477AA", "QUEUE_ONLY": "#228833", "ADAPTIVE": "#EE7733"}
    values = {(row["scenario"], row["controller"], row["metric"]): row for row in summary}
    for metric, (title, units) in PLOTS.items():
        figure, axes = plt.subplots(figsize=(11, 5.5), layout="constrained")
        width = 0.8 / len(controllers)
        for index, controller in enumerate(controllers):
            offset = (index - (len(controllers) - 1) / 2) * width
            records = [values[(scenario, controller, metric)] for scenario in scenarios]
            axes.bar(
                [index + offset for index in range(len(scenarios))],
                [record["mean"] for record in records], width=width * 0.95,
                # Для n=1 рисуем столбец без видимой полосы разброса.
                yerr=[record["std"] or 0.0 for record in records],
                capsize=3, color=colors[controller], label=controller,
            )
        axes.set_xticks(range(len(scenarios)), [name.replace("_", "\n") for name in scenarios])
        counts = ", ".join(str(n) for n in sorted({row["seeds"] for row in summary}))
        axes.set_title(f"{title}\nPaired seeds per scenario: {counts}; bars: mean; error bars: sample SD (n > 1)")
        axes.set_ylabel(units)
        axes.set_ylim(bottom=0)
        axes.grid(axis="y", alpha=0.25)
        axes.set_axisbelow(True)
        axes.legend()
        figure.savefig(output / f"{metric}.png", dpi=180)
        figure.savefig(output / f"{metric}.svg")
        plt.close(figure)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analyze paired AdaptiveTraffic results")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path, help="Output directory; default: analysis beside CSV")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows = load_results(args.csv_path)
        paired_rows, exclusions = select_complete_pairs(rows)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    output = args.output or args.csv_path.parent / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    group_size = 3 if any(row["controller"] == "QUEUE_ONLY" for row in rows) else 2
    group_label = "pairs" if group_size == 2 else "groups"
    report = [
        f"Source: {args.csv_path.resolve()}", f"Input runs: {len(rows)}",
        f"Complete {group_label} used: {len(paired_rows) // group_size}",
        f"Excluded pairs: {len(exclusions)}", *exclusions,
        "An incomplete seed excludes ALL compared controllers. Exclusion can bias results; inspect failures.",
        "Means and sample SD are computed across seeds; SD is undefined for one seed.",
        "Positive improvement_percent means improvement; undefined percentages are blank.",
        "total_clearing_time includes generation and drain; counts are not throughput.",
        "Small seed counts are a technical check, not evidence of general effectiveness.",
    ]
    (output / "analysis_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    for exclusion in exclusions:
        print(exclusion)
    if not paired_rows:
        print(f"No complete pairs to compare. Report: {output / 'analysis_report.txt'}")
        return 1
    summary, comparisons = summarize(paired_rows)
    write_table(output / "summary.csv", summary)
    write_table(output / "comparison.csv", comparisons)
    pairwise = compare_all_pairs(paired_rows)
    write_table(output / "pairwise_comparison.csv", pairwise)
    if not args.no_plots:
        create_plots(summary, output)
    print("scenario          seeds  FIXED mean_wait  ADAPTIVE mean_wait  improvement")
    for row in comparisons:
        if row["metric"] == "mean_wait":
            improvement = row["improvement_percent"]
            label = "undefined" if improvement is None else f"{improvement:+.2f}%"
            print(f"{row['scenario']:<18} {row['seeds']:>3} {row['fixed_mean']:>15.2f} {row['adaptive_mean']:>19.2f} {label:>12}")
    if group_size == 3:
        print("QUEUE_ONLY vs ADAPTIVE (mean_wait):")
        for row in pairwise:
            if row["baseline"] == "QUEUE_ONLY" and row["metric"] == "mean_wait":
                print(f"{row['scenario']}: QUEUE_ONLY={row['baseline_mean']:.2f}; ADAPTIVE={row['controller_mean']:.2f}")
    print(f"Analysis: {output}")
    print("Small seed counts are a technical check; inspect incomplete pairs before interpreting results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
