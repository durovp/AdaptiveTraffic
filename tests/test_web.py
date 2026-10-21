import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from adaptive_traffic.config import Config
from adaptive_traffic.controllers import CONTROLLERS
from adaptive_traffic.metrics import calculate_metrics
from adaptive_traffic.models import Direction
from adaptive_traffic.scenarios import create_custom_plan, create_traffic_plan
from adaptive_traffic.simulation import Simulation
from adaptive_traffic.web import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path/'results', testing=True)
    with TestClient(app) as client:
        yield client


def create(client, **changes):
    response = client.post('/api/sessions', json=dict(duration=30, **changes))
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_web_import_does_not_load_pygame():
    subprocess.run([sys.executable, '-c', "import sys; import adaptive_traffic.web; assert 'pygame' not in sys.modules"],check=True)


@pytest.mark.parametrize('path', ['/', '/static/style.css', '/static/app.js', '/static/icon.svg', '/api/project', '/api/scenarios', '/api/reference-results'])
def test_local_assets_and_metadata(client, path):
    assert client.get(path).status_code == 200


def test_no_remote_asset_dependency(client):
    html = client.get('/').text
    assert 'cdn.' not in html and 'fonts.googleapis' not in html
    assert client.get('/docs').status_code == 404


@pytest.mark.parametrize('controller', list(CONTROLLERS))
def test_step_finish_and_headless_identity(client, controller):
    key = create(client, controllers=[controller], interactive=False)
    view = client.post(f'/api/sessions/{key}/advance', json={'steps':1}).json()
    assert view['time'] == 1 and view['views'][0]['time'] == 1
    data = client.post(f'/api/sessions/{key}/finish').json()
    assert data['finished'] and data['views'][0]['completed_successfully']
    c = Config(simulation_duration=30.0)
    result = Simulation(c, create_traffic_plan('NS_HEAVY',42,30.0), CONTROLLERS[controller](c)).run()
    assert {k:v for k,v in data['results'][0].items() if k!='diagnostics'} == calculate_metrics(result)
    assert client.get(f'/api/sessions/{key}/export').status_code == 200


def test_pedestrian_signal_is_safe_for_all_controllers(client):
    key = client.post('/api/comparison', json={'controllers':list(CONTROLLERS), 'duration':80}).json()['id']
    assert client.post(f'/api/sessions/{key}/pedestrian',json={}).status_code == 200
    seen = set()
    for _ in range(80):
        data=client.post(f'/api/sessions/{key}/advance',json={'steps':1}).json()
        for v in data['views']:
            greens={d for d,s in v['signals'].items() if s=='GREEN'}
            assert not (greens.intersection({'NORTH','SOUTH'}) and greens.intersection({'EAST','WEST'}))
            if v['pedestrian_signal']=='GREEN':
                seen.add(v['controller']);assert not greens
    assert seen == set(CONTROLLERS)


def test_manual_disabled_by_default_in_model_and_noninteractive_api(client):
    c=Config(simulation_duration=30)
    sim=Simulation(c,create_traffic_plan('NS_HEAVY',42,30),CONTROLLERS['FIXED'](c))
    with pytest.raises(ValueError, match='интерактивной'):
        sim.inject(Direction.NORTH,5)
    key=create(client,interactive=False)
    assert client.post(f'/api/sessions/{key}/inject',json={'directions':['NORTH'],'count':5}).status_code==422


def test_injection_preserves_plan_and_fifo_then_counts_real_cars(client):
    key=create(client)
    session=client.app.state.store.get(key)
    plan=session.plan
    client.post(f'/api/sessions/{key}/advance',json={'steps':5})
    before=client.get(f'/api/sessions/{key}').json()
    reply=client.post(f'/api/sessions/{key}/inject',json={'directions':['NORTH'],'count':10})
    assert reply.status_code==200
    assert session.plan is plan and all(s.plan is plan for s in session.simulations)
    assert reply.json()['views'][0]['manual_count']==10
    sim=session.simulations[0]
    arrival_times=[v.arrival_time for v in sim.queues[Direction.NORTH]]
    assert arrival_times==sorted(arrival_times)
    final=client.post(f'/api/sessions/{key}/finish').json()['results'][0]
    assert final['vehicles_generated']==len(plan.arrivals)+10==final['vehicles_passed']
    assert final['completed_successfully']
    assert client.post(f'/api/sessions/{key}/inject',json={'directions':['NORTH'],'count':1}).status_code==422


def test_comparison_replays_same_events_and_reuses_exact_plan(client):
    source=create(client)
    client.post(f'/api/sessions/{source}/advance',json={'steps':5})
    client.post(f'/api/sessions/{source}/inject',json={'directions':['NORTH','SOUTH'],'count':10})
    client.post(f'/api/sessions/{source}/pedestrian',json={})
    client.post(f'/api/sessions/{source}/advance',json={'steps':7})
    response=client.post('/api/comparison',json={'source_id':source,'controllers':list(CONTROLLERS)})
    assert response.status_code==200,response.text
    target=client.app.state.store.get(response.json()['id']); original=client.app.state.store.get(source)
    assert all(s.plan is original.plan for s in target.simulations)
    assert target.events==original.events
    assert all(s.time==12 and s.manual_count==20 for s in target.simulations)
    assert get_metrics(target.simulations[-1])==get_metrics(original.simulations[0])
    manual=[[(v.id,v.direction,v.arrival_time) for v in s.vehicles[len(s.plan.arrivals):]] for s in target.simulations]
    assert all(events==manual[0] for events in manual)
    client.post(f'/api/sessions/{target.id}/inject',json={'directions':['EAST'],'count':5})
    assert all(s.manual_count==25 for s in target.simulations)


def get_metrics(sim):
    return calculate_metrics(sim.result())


def test_restart_switches_controller_without_regenerating_plan(client):
    key=create(client)
    session=client.app.state.store.get(key); plan=session.plan
    client.post(f'/api/sessions/{key}/inject',json={'directions':['WEST'],'count':5})
    reply=client.post(f'/api/sessions/{key}/restart',json={'controllers':['FIXED']})
    assert reply.json()['time']==0 and reply.json()['views'][0]['manual_count']==0
    assert session.plan is plan and session.events==[]
    assert client.post(f'/api/sessions/{key}/restart',json={'controllers':[]}).status_code==422


def test_custom_uses_reproducible_generator(client):
    a=create_custom_plan(12,60,(10,5,2,0)); b=create_custom_plan(12,60,(10,5,2,0))
    assert a==b and a!=create_custom_plan(13,60,(10,5,2,0))
    key=create(client,scenario='CUSTOM',custom_rates=[10,5,2,0])
    assert client.app.state.store.get(key).plan == create_custom_plan(42,30.0,(10,5,2,0))


@pytest.mark.parametrize('body', [
    {'settings':{'min_green':50,'max_green':20}}, {'settings':{'switch_margin':-1}},
    {'settings':{'v2_balance_ratio':2}}, {'settings':{'yellow':0}}, {'controllers':['UNKNOWN']},
    {'custom_rates':[-1,0,0,0]}, {'custom_rates':[30,0,0,0]}, {'duration':0},
    {'pedestrian_times':[20,5]}, {'pedestrian_times':[30]}, {'scenario':'ALL'},
])
def test_invalid_input_returns_explanation(client,body):
    response=client.post('/api/sessions',json={'duration':30,**body})
    assert response.status_code==422 and response.json()['detail']


def test_same_origin_and_path_protection(client):
    assert client.post('/api/sessions',json={},headers={'Origin':'https://outside.example'}).status_code==403
    assert client.get('/api/project',headers={'Host':'outside.example'}).status_code==400
    assert client.get('/static/../requirements.txt').status_code==404
    assert client.get('/api/experiments/missing/files/secret.txt').status_code==422


def test_idle_expiry_and_bounded_session_count(client):
    store=client.app.state.store;key=create(client)
    store.sessions[key].touched-=store.ttl+1
    store.cleanup();assert client.get(f'/api/sessions/{key}').status_code==404
    store.capacity=1
    create(client)
    assert client.post('/api/sessions',json={'duration':30}).status_code==429


@pytest.mark.parametrize('controllers', [['FIXED'], ['FIXED','ADAPTIVE_V2'], list(CONTROLLERS)])
def test_laboratory_creates_real_csv_and_progress(client,controllers):
    response=client.post('/api/experiments',json={'duration':30,'runs':2,'controllers':controllers,'scenario':'CUSTOM','custom_rates':[10,10,5,5]})
    assert response.status_code==200,response.text
    job=response.json();deadline=time.monotonic()+10
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.01);job=client.get(f"/api/experiments/{job['id']}").json()
    assert job['status']=='complete',job
    assert job['done']==job['total']==2*len(controllers)
    assert not job['exclusions'] and job['summary']
    for filename in ('results.csv','diagnostics.csv'):
        r=client.get(job['downloads'][filename]);assert r.status_code==200
        assert len(r.text.strip().splitlines())==1+job['total']
        assert (Path(job['folder'])/filename).exists()
    assert (Path(job['folder'])/'analysis/summary.csv').exists()
    assert client.post('/api/experiments',json={'runs':51}).status_code==422
    assert client.post('/api/experiments',json={'interactive':True}).status_code==422


def test_reference_config_is_frozen_and_real():
    root=Path(__file__).parents[1]
    ref=json.loads((root/'web/data/reference_results.json').read_text())
    chosen=json.loads((root/'docs/v2_selection.json').read_text())
    assert ref['config']==chosen['config']
    assert ref['seeds']==list(range(100,120)) and chosen['seeds']==list(range(42,52))
    assert ref['total_runs']==560 and ref['incomplete_runs']==0 and len(ref['metrics'])==28
