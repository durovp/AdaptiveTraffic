from dataclasses import replace
import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from adaptive_traffic.server_config import ServerConfig
from adaptive_traffic.web import create_app

TOKEN='test-only-administrator-key-32-characters'


@pytest.fixture
def hosted(tmp_path):
    policy=ServerConfig(production=True,hosts=('traffic.example',),origins=('https://traffic.example',),
                        admin_token=TOKEN,session_capacity=2,max_duration=600)
    with TestClient(create_app(tmp_path,server_config=policy),base_url='https://traffic.example',
                    headers={'Origin':'https://traffic.example'}) as client:
        yield client


def test_production_headers_origins_hosts_and_health(hosted):
    r=hosted.get('/healthz');assert r.json()=={'status':'ok'}
    assert r.headers['permissions-policy']=='serial=(self)'
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert hosted.get('/',headers={'Host':'evil.example'}).status_code==400
    assert hosted.post('/api/sessions',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert hosted.post('/api/sessions',json={},headers={'Origin':'null'}).status_code==403
    hosted.headers.pop('Origin')
    assert hosted.post('/api/sessions',json={}).status_code==403


def test_laboratory_requires_secret_only_on_hosted_server(hosted):
    assert hosted.post('/api/experiments',json={}).status_code==401
    assert hosted.post('/api/experiments',json={},headers={'Authorization':'Bearer wrong'}).status_code==401
    reply=hosted.post('/api/experiments',json={'duration':10,'runs':1},headers={'Authorization':'Bearer '+TOKEN})
    assert reply.status_code==200,reply.text
    job=reply.json();deadline=time.monotonic()+10
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.01);job=hosted.get('/api/experiments/'+job['id']).json()
    assert job['status']=='complete' and job['completed_runs']==1
    assert TOKEN not in hosted.get('/api/project').text


def test_body_duration_and_capacity_limits(hosted):
    assert hosted.post('/api/sessions',content=b'x'*16385).status_code==413
    assert hosted.post('/api/sessions',json={'duration':601}).status_code==422
    for _ in range(2): assert hosted.post('/api/sessions',json={'duration':10}).status_code==200
    assert hosted.post('/api/sessions',json={'duration':10}).status_code==429
    assert len(hosted.app.state.store.sessions)==2


def test_request_flood_is_bounded(tmp_path):
    policy=ServerConfig(production=True,requests_per_minute=2)
    with TestClient(create_app(tmp_path,testing=True,server_config=policy)) as c:
        assert [c.get('/api/project').status_code for _ in range(3)]==[200,200,429]


def test_hardware_optimistic_revision_prevents_double_steps(hosted):
    d=hosted.post('/api/sessions',json={'duration':10}).json();key=d['id']
    body={'steps':1,'expected_revision':d['revision']}
    assert hosted.post(f'/api/sessions/{key}/advance',json=body).status_code==200
    assert hosted.post(f'/api/sessions/{key}/advance',json=body).status_code==409
    final=hosted.get(f'/api/sessions/{key}').json()
    assert final['time']==1 and final['revision']==d['revision']+1


@pytest.mark.parametrize('changes',[{'ALLOWED_HOSTS':'*'}, {'ALLOWED_ORIGINS':'http://traffic.example'},
                                  {'ADMIN_TOKEN':'short'}, {'MAX_SESSIONS':'100'}])
def test_invalid_public_configuration_fails_startup(monkeypatch,changes):
    for name,value in {'TRAFFIC_ENV':'production','ALLOWED_HOSTS':'traffic.example',
                       'ALLOWED_ORIGINS':'https://traffic.example',**changes}.items(): monkeypatch.setenv(name,value)
    with pytest.raises(ValueError): ServerConfig.from_env()


def test_disk_limit_includes_nested_switch_traces(hosted):
    trace = hosted.app.state.lab.root / 'web_previous' / 'switches' / 'trace.csv'
    trace.parent.mkdir(parents=True)
    with trace.open('wb') as output:
        output.truncate(100 * 1024 * 1024)
    response = hosted.post('/api/experiments', json={'duration':10,'runs':1},
                           headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 507
    assert not hosted.app.state.lab.jobs
    assert trace.exists()  # Research results must never be deleted automatically.


def test_stalled_body_is_rejected_before_creating_session(hosted, monkeypatch):
    from adaptive_traffic.web_security import PublicPolicy
    monkeypatch.setattr(PublicPolicy, 'body_timeout', 0.02)
    called = False
    messages = []
    async def app(scope, receive, send):
        nonlocal called
        called = True
    async def receive():
        await asyncio.sleep(1)
        return {'type':'http.request','body':b'','more_body':False}
    async def send(message):
        messages.append(message)
    policy = PublicPolicy(app, hosted.app.state.policy)
    scope = {'type':'http','method':'POST','path':'/api/sessions','scheme':'https',
             'headers':[(b'host',b'traffic.example'),(b'origin',b'https://traffic.example')],
             'client':('127.0.0.1',12345)}
    asyncio.run(policy(scope, receive, send))
    assert messages[0]['status'] == 408
    assert not called and policy.active_mutations == 0


def test_non_utf8_header_does_not_crash_policy(hosted):
    assert hosted.get('/healthz',headers={b'x-extra':b'\xff'}).status_code == 200
