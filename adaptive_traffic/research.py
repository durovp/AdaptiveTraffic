"""Bounded development study and a separate, frozen-config holdout run.

python -m adaptive_traffic.research development BASELINE_DIR OUTPUT_DIR
python -m adaptive_traffic.research holdout DEVELOPMENT_DIR OUTPUT_DIR
"""
import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import statistics

from .config import Config, SCENARIO_PROFILES
from .controllers import AdaptiveV2Controller, CONTROLLERS
from .experiments import run_experiments
from .metrics import calculate_metrics, calculate_diagnostics
from .scenarios import create_traffic_plan
from .simulation import Simulation


TARGETS = ('BALANCED_HIGH', 'RUSH_HOUR')


def read_csv(path):
    with Path(path).open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def aggregate(results, diagnostics):
    """Equal weight per seed; exclude the whole scenario/seed if any run failed."""
    keys = {(r['scenario'], str(r['seed'])) for r in results if str(r['completed_successfully']) != 'True'}
    groups = {}
    for source in (results, diagnostics):
        for row in source:
            if (row['scenario'], str(row['seed'])) in keys:
                continue
            group = groups.setdefault((row['scenario'], row['controller']), {})
            for key, value in row.items():
                if key not in ('scenario', 'controller', 'seed', 'completed_successfully'):
                    group.setdefault(key, []).append(float(value))
    return [dict(scenario=s, controller=c, metrics={k: statistics.mean(v) for k, v in metrics.items()})
            for (s, c), metrics in sorted(groups.items())]


def development(baseline, output):
    output.mkdir(parents=True, exist_ok=False)
    base_meta = json.loads((baseline / 'metadata.json').read_text())
    assert base_meta['seeds'] == list(range(42, 52)), 'Development seeds must be 42–51'
    config = Config(**base_meta['config'])
    base_rows = read_csv(baseline / 'results.csv')
    assert len(base_rows) == 210 and all(r['completed_successfully'] == 'True' for r in base_rows)
    base = {(r['scenario'], r['controller']): r['metrics'] for r in aggregate(base_rows, read_csv(baseline / 'diagnostics.csv'))}
    plans = [create_traffic_plan(s, seed, config.simulation_duration) for s in SCENARIO_PROFILES for seed in range(42, 52)]
    candidates = []
    for threshold, ratio, minimum in product((6, 8, 10), (.65, .75), (20, 25, 30)):
        parameters = dict(v2_busy_queue_threshold=threshold, v2_balance_ratio=ratio, v2_balanced_min_green=minimum)
        cfg = replace(config, **parameters)
        rows, diagnostics = [], []
        for plan in plans:
            result = Simulation(cfg, plan, AdaptiveV2Controller(cfg)).run()
            rows.append(calculate_metrics(result))
            diagnostics.append(calculate_diagnostics(result))
        summary = aggregate(rows, diagnostics)
        target = {r['scenario']: r['metrics'] for r in summary}
        violations = {}
        for scenario in SCENARIO_PROFILES:
            original, candidate = base[scenario, 'ADAPTIVE'], target[scenario]
            limits = {'p95_wait': original['p95_wait'] * 1.15}
            if scenario not in TARGETS:
                limits['mean_wait'] = max(original['mean_wait'] * 1.10, original['mean_wait'] + 2)
            else:
                limits['mean_wait'] = original['mean_wait']
            for metric, limit in limits.items():
                if candidate[metric] > limit:
                    violations[f'{scenario}/{metric}'] = candidate[metric] / limit - 1
        incomplete = sum(not row['completed_successfully'] for row in rows)
        score = statistics.mean(target[s]['mean_wait'] / base[s, 'ADAPTIVE']['mean_wait'] for s in TARGETS)
        record = dict(parameters=parameters, score=score, violations=violations, incomplete=incomplete, summary=summary)
        candidates.append(record)
        folder = output / f'q{threshold}_r{ratio}_g{minimum}'
        folder.mkdir()
        for filename, records in (('results.csv', rows), ('diagnostics.csv', diagnostics)):
            with (folder / filename).open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)
        print(parameters, 'score', round(score, 4), 'violations', violations, flush=True)
    chosen = min(candidates, key=lambda r: (r['incomplete'], bool(r['violations']), sum(r['violations'].values()), r['score']))
    selection = dict(created_at=datetime.now(timezone.utc).isoformat(), seeds=list(range(42, 52)),
                     selection_rule='incomplete, any guardrail violation, total relative violation, mean target ratio',
                     config=asdict(replace(config, **chosen['parameters'])), chosen=chosen, candidates=candidates)
    (output / 'selection.json').write_text(json.dumps(selection, indent=2)+'\n')
    print('SELECTED', chosen['parameters'], flush=True)


def holdout(development_dir, output):
    selection_path = development_dir / 'selection.json'
    selection = json.loads(selection_path.read_text())
    config = Config(**selection['config'])
    run_experiments(config, list(SCENARIO_PROFILES), list(range(100, 120)), output, 'all')
    metadata = json.loads((output / 'metadata.json').read_text())
    summary = dict(seeds=list(range(100, 120)), config=asdict(config), scenarios=list(SCENARIO_PROFILES),
                   controllers=list(CONTROLLERS), generated_at=metadata['created_at'],
                   development_selection_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest(),
                   results_sha256=hashlib.sha256((output / 'results.csv').read_bytes()).hexdigest(),
                   source=str(output), python_version=metadata['python_version'],
                   metrics=aggregate(read_csv(output/'results.csv'), read_csv(output/'diagnostics.csv')),
                   total_runs=560, incomplete_runs=sum(r['completed_successfully']!='True' for r in read_csv(output/'results.csv')))
    (output / 'reference_results.json').write_text(json.dumps(summary, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('development', 'holdout'))
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    (development if args.stage == 'development' else holdout)(args.source, args.output)


if __name__ == '__main__':
    main()
