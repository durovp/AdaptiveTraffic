"""Recorded before V2/web: exact numeric results AND every switch for old controllers."""
import ast
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from adaptive_traffic.config import Config
from adaptive_traffic.controllers import CONTROLLERS
from adaptive_traffic.experiments import SWITCH_FIELDS
from adaptive_traffic.metrics import calculate_metrics
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation

BASELINE = json.loads((Path(__file__).parent / 'fixtures/legacy_baseline.json').read_text())


def test_old_controller_source_is_unchanged():
    source = (Path(__file__).parents[1] / 'adaptive_traffic/controllers.py').read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name in BASELINE['controller_source_sha256']:
            assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == BASELINE['controller_source_sha256'][node.name]


@pytest.mark.parametrize('expected', BASELINE['runs'], ids=lambda r: r['scenario']+'-'+r['controller'])
def test_old_plan_metrics_and_switches_are_bit_exact(expected):
    config = Config()
    result = Simulation(config, create_traffic_plan(expected['scenario'], 42, config.simulation_duration), CONTROLLERS[expected['controller']](config)).run()
    assert {k: str(v) for k,v in calculate_metrics(result).items()} == {k:v for k,v in expected.items() if k!='switch_csv_sha256'}
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=SWITCH_FIELDS)
    writer.writeheader(); writer.writerows(result.switches)
    assert hashlib.sha256(stream.getvalue().encode()).hexdigest() == expected['switch_csv_sha256']
