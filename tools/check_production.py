"""Real HTTP smoke/stress check; starts an owned server unless --url is supplied.

Uses only the standard library. External checks create/delete their own sessions
and one small lab job; use only against an instance you administer.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def check(url, origin, token, stress):
    created = []
    report = {}

    def request(path, body=None, *, method=None, expected=200, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request_headers = {'Origin': origin, 'Content-Type': 'application/json'}
        request_headers.update(headers or {})
        deadline = time.monotonic() + 90
        while True:
            req = Request(url + path, data=data, headers=request_headers, method=method)
            try:
                response = urlopen(req, timeout=90)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read()
                status = response.status
                content_type = response.headers.get('Content-Type', '')
            if status == 429 and expected != 429 and time.monotonic() < deadline:
                time.sleep(1)
                continue
            if status != expected:
                raise AssertionError(f'{path}: expected {expected}, received {status}')
            return json.loads(raw) if 'application/json' in content_type else raw

    try:
        assert request('/healthz') == {'status': 'ok'}
        for path in ('/', '/static/app.js', '/static/serial.js', '/static/style.css', '/static/icon.svg'):
            assert request(path)
        project = request('/api/project')
        assert project['hosting']['production']
        assert project['hardware']['protocol'] == 1
        assert len(project['controllers']) == 4
        assert request('/api/reference-results')
        request('/api/sessions', {}, expected=403, headers={'Origin':'https://untrusted.example'})
        request('/api/experiments', {}, expected=401)
        spec = {'scenario':'CUSTOM','custom_rates':[25,25,25,25],
                'controllers':project['controllers'],'duration':600 if stress else 30}
        session = request('/api/sessions', spec)
        created.append(session['id'])
        path = '/api/sessions/' + session['id']
        session = request(path + '/inject', {'directions':['NORTH','SOUTH'],'count':5})
        assert session['event_count'] == 1
        session = request(path + '/pedestrian', {})
        assert session['event_count'] == 2
        comparison = request('/api/comparison', {'source_id':session['id'],
                             'controllers':['FIXED','ADAPTIVE_V2']})
        created.append(comparison['id'])
        assert comparison['event_count'] == 2
        assert len(comparison['views']) == 2
        result = request('/api/sessions/' + comparison['id'] + '/finish', {})
        assert result['finished'] and len(result['results']) == 2
        request('/api/sessions/' + comparison['id'], method='DELETE')
        created.remove(comparison['id'])

        if stress:
            while len(created) < 8:
                created.append(request('/api/sessions',spec)['id'])
            request('/api/sessions', spec, expected=429)
            for key in created:
                # 960 injected cars per session, plus the initial test event.
                for _ in range(12):
                    request('/api/sessions/' + key + '/inject',
                            {'directions':['NORTH','SOUTH','EAST','WEST'],'count':20})
        job = request('/api/experiments', {'scenario':'ALL' if stress else 'NS_HEAVY',
                      'controllers':project['controllers'],'duration':600 if stress else 30,
                      'runs':2 if stress else 1}, headers={'Authorization':'Bearer ' + token})
        request('/api/experiments', {}, expected=409,
                headers={'Authorization':'Bearer ' + token}) if stress else None
        for key in created:
            result = request('/api/sessions/' + key + '/finish', {})
            assert result['finished'] and len(result['results']) == 4
        deadline = time.monotonic() + 150
        while job['status'] in ('queued', 'running'):
            assert time.monotonic() < deadline, 'Laboratory deadline exceeded'
            time.sleep(0.5)
            job = request('/api/experiments/' + job['id'])
        assert job['status'] == 'complete', 'Laboratory did not complete'
        assert job['done'] == (56 if stress else 4)
        for path in job['downloads'].values():
            assert request(path)
        assert request('/healthz') == {'status':'ok'}
        report.update(status='PASS', production=True, static_assets=True,
                      controllers=project['controllers'], comparison=True,
                      injection=True, pedestrian=True, laboratory_runs=job['done'],
                      concurrent_sessions=len(created), manual_cars_per_stress_session=960 if stress else 5,
                      simulation_duration=spec['duration'], hardware_assets=True)
        return report
    finally:
        for key in created:
            request('/api/sessions/' + key, method='DELETE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Existing instance; ADMIN_TOKEN must be in the environment')
    parser.add_argument('--origin', default='https://traffic.example')
    parser.add_argument('--stress', action='store_true', help='Exercise default production capacity (8 sessions)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.url:
        token = os.environ.get('ADMIN_TOKEN')
        if not token:
            parser.error('ADMIN_TOKEN is required for the laboratory check')
        report = check(args.url.rstrip('/'), args.origin, token, args.stress)
    else:
        with tempfile.TemporaryDirectory(prefix='adaptive-production-') as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port = sock.getsockname()[1]
            token = secrets.token_urlsafe(32)
            env = dict(os.environ, TRAFFIC_ENV='production', HOST='127.0.0.1', PORT=str(port),
                       ALLOWED_HOSTS='127.0.0.1,traffic.example', ALLOWED_ORIGINS=args.origin,
                       ADMIN_TOKEN=token, RESULTS_DIR=directory, MAX_SESSIONS='8', MAX_DURATION='600')
            url = f'http://127.0.0.1:{port}'
            memory = {'rss_peak_kib':0,'threads_peak':0}
            stopped = threading.Event()
            with tempfile.TemporaryFile(mode='w+') as logs:
                process = subprocess.Popen([sys.executable,'-m','adaptive_traffic.serve'],
                                           cwd=ROOT, env=env, stdout=logs, stderr=subprocess.STDOUT)
                def monitor():
                    while not stopped.wait(0.05):
                        try:
                            fields = dict(line.split(':',1) for line in Path(f'/proc/{process.pid}/status').read_text().splitlines())
                            memory['rss_peak_kib'] = max(memory['rss_peak_kib'], int(fields.get('VmHWM','0').split()[0]))
                            memory['threads_peak'] = max(memory['threads_peak'],int(fields.get('Threads','0')))
                        except (FileNotFoundError, ProcessLookupError):
                            break
                monitor_thread = threading.Thread(target=monitor,daemon=True)
                monitor_thread.start()
                try:
                    deadline = time.monotonic()+30
                    while True:
                        try:
                            with urlopen(url+'/healthz', timeout=1) as response:
                                assert response.status == 200
                            break
                        except (URLError, TimeoutError):
                            if process.poll() is not None or time.monotonic()>deadline:
                                raise RuntimeError('Production server failed to start')
                            time.sleep(0.1)
                    report = check(url,args.origin,token,args.stress)
                    report.update(memory)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError('Production server did not shut down gracefully')
                    stopped.set()
                    monitor_thread.join()
                logs.seek(0)
                report['graceful_shutdown'] = 'Application shutdown complete' in logs.read()
                assert report['graceful_shutdown']
    serialized = json.dumps(report,indent=2,ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(serialized+'\n')
    print(serialized)


if __name__ == '__main__':
    main()
