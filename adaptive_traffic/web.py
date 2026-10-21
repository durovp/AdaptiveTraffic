"""Offline, loopback-only demonstration API. The Python model owns all decisions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
from threading import RLock, Timer
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analyze import compare_all_pairs, load_results, select_complete_pairs, summarize, write_table
from .config import Config, SCENARIO_PROFILES
from .controllers import CONTROLLERS
from .experiments import run_experiments
from .metrics import calculate_metrics, calculate_diagnostics
from .models import Direction
from .presentation import get_view_state
from .research import aggregate, read_csv
from .scenarios import create_traffic_plan, create_custom_plan
from .simulation import Simulation

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
SETTINGS = ('min_green', 'max_green', 'max_wait', 'wait_equivalent', 'switch_margin',
            'v2_busy_queue_threshold', 'v2_balance_ratio', 'v2_balanced_min_green')
SCENARIO_DESCRIPTIONS = {
    'BALANCED_LOW': 'Небольшой одинаковый поток со всех сторон — 4 авто/мин на подход.',
    'BALANCED_HIGH': 'Высокий одинаковый поток — 12 авто/мин на подход.',
    'NS_HEAVY': 'Основной поток север–юг: 20 против 4 авто/мин.',
    'EW_HEAVY': 'Основной поток восток–запад: 20 против 4 авто/мин.',
    'CHANGING_FLOW': 'Поток меняется: север–юг, равномерный, восток–запад.',
    'RUSH_HOUR': 'Час пик: интенсивность растёт с 6 до 18 авто/мин на подход.',
    'MINOR_ROAD': 'Главная дорога 18 авто/мин, редкие машины на боковой — 0,5.',
    'CUSTOM': 'Постоянный поток: задайте интенсивность каждого подхода.',
}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class SessionSpec(Input):
    scenario: str = 'NS_HEAVY'
    controllers: list[str] = Field(default_factory=lambda: ['ADAPTIVE_V2'], min_length=1, max_length=4)
    seed: int = Field(default=42, ge=0, le=2**31-1)
    duration: int = Field(default=180, ge=10, le=3600)
    interactive: bool = True
    custom_rates: tuple[float, float, float, float] = (12, 12, 4, 4)
    pedestrian_times: list[float] = Field(default_factory=list, max_length=200)
    settings: dict[str, float] = Field(default_factory=dict)

    def config(self):
        return Config(simulation_duration=float(self.duration), **self.settings)

    def plan(self):
        if self.scenario == 'CUSTOM':
            return create_custom_plan(self.seed, float(self.duration), self.custom_rates)
        return create_traffic_plan(self.scenario, self.seed, float(self.duration))

    @model_validator(mode='after')
    def validate_input(self):
        if self.scenario not in SCENARIO_DESCRIPTIONS and self.scenario != 'ALL':
            raise ValueError('Неизвестный сценарий')
        if len(set(self.controllers)) != len(self.controllers) or any(c not in CONTROLLERS for c in self.controllers):
            raise ValueError('Выберите различные известные контроллеры')
        if any(not 0 <= r <= 25 for r in self.custom_rates):
            raise ValueError('Интенсивность должна быть от 0 до 25 авто/мин')
        if set(self.settings) - set(SETTINGS):
            raise ValueError('Изменение этого параметра не разрешено')
        if any(v > 600 for v in self.settings.values()):
            raise ValueError('Параметр не должен превышать 600')
        self.config()  # Shared domain validation, including timing grid and MIN <= MAX.
        if self.pedestrian_times != sorted(self.pedestrian_times) or any(not 0 <= t < self.duration for t in self.pedestrian_times):
            raise ValueError('Времена пешеходов: по возрастанию, от 0 до окончания генерации')
        return self


class ComparisonSpec(SessionSpec):
    controllers: list[str] = Field(default_factory=lambda: ['FIXED', 'ADAPTIVE_V2'], min_length=2, max_length=4)
    source_id: str | None = None


class ExperimentSpec(SessionSpec):
    runs: int = Field(default=10, ge=1, le=50)
    interactive: Literal[False] = False


class Advance(Input):
    steps: int = Field(default=1, ge=1, le=1000)


class Injection(Input):
    directions: list[Direction] = Field(min_length=1, max_length=4)
    count: int = Field(default=5, ge=1, le=20)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.directions)) != len(self.directions):
            raise ValueError('Направления не должны повторяться')
        return self


class Restart(Input):
    controllers: list[str] | None = None


class Session:
    def __init__(self, spec: SessionSpec, plan=None):
        if spec.scenario == 'ALL':
            raise ValueError('Для симулятора выберите один сценарий')
        self.id = uuid4().hex
        self.spec = spec
        self.config = spec.config()
        self.plan = plan if plan is not None else spec.plan()
        self.events = []
        self.touched = time.monotonic()
        self.lock = RLock()
        self.reset()

    def reset(self):
        self.simulations = [Simulation(self.config, self.plan, CONTROLLERS[name](self.config),
                                      tuple(self.spec.pedestrian_times), allow_injection=self.spec.interactive)
                            for name in self.spec.controllers]
        self.events = []

    @property
    def time(self):
        return max(s.time for s in self.simulations)

    def advance(self, steps):
        for _ in range(steps):
            if all(s.finished for s in self.simulations):
                break
            for sim in self.simulations:
                sim.step()

    def event(self, kind, payload):
        if not self.spec.interactive:
            raise ValueError('Ручные события разрешены только в интерактивной демонстрации')
        # End-of-generation cutoff keeps events identical even when drain lengths differ.
        if self.time >= self.plan.duration or any(s.finished for s in self.simulations):
            raise ValueError('Генерация завершена; перезапустите демонстрацию')
        if len(self.events) >= 1000:
            raise ValueError('Достигнут лимит событий; перезапустите демонстрацию')
        if kind == 'inject':
            count = payload['count'] * len(payload['directions'])
            if any(s.manual_count + count > 1000 for s in self.simulations):
                raise ValueError('Лимит 1000 ручных машин; перезапустите демонстрацию')
            for sim in self.simulations:
                for direction in payload['directions']:
                    sim.inject(Direction(direction), payload['count'])
        else:
            for sim in self.simulations:
                sim.request_pedestrian()
        self.events.append(dict(time=self.time, kind=kind, **payload))

    def view(self):
        finished = all(s.finished for s in self.simulations)
        result = dict(id=self.id, spec=self.spec.model_dump(), time=self.time,
                      finished=finished, event_count=len(self.events),
                      views=[get_view_state(s) for s in self.simulations])
        if finished:
            result['results'] = [dict(**calculate_metrics(s.result()), diagnostics=calculate_diagnostics(s.result())) for s in self.simulations]
            rows = result['results']
            result['comparisons'] = compare_all_pairs(rows) if len(rows)>1 and all(r['completed_successfully'] for r in rows) else []
        return result


class Store:
    """Bounded in-memory sessions, serialized mutations, idle expiry."""
    def __init__(self, ttl=1800, capacity=32):
        self.sessions = {}
        self.lock = RLock()
        self.ttl, self.capacity = ttl, capacity

    def cleanup(self):
        with self.lock:
            now = time.monotonic()
            for key, session in list(self.sessions.items()):
                if now - session.touched > self.ttl and session.lock.acquire(blocking=False):
                    try:
                        del self.sessions[key]
                    finally:
                        session.lock.release()

    def add(self, session):
        with self.lock:
            self.cleanup()
            if len(self.sessions) >= self.capacity:
                raise HTTPException(429, 'Слишком много сессий. Закройте лишние или подождите 30 минут.')
            self.sessions[session.id] = session
        return session

    def get(self, key):
        with self.lock:
            self.cleanup()
            if key not in self.sessions:
                raise HTTPException(404, 'Сессия не найдена или истекла. Создайте новую.')
            session = self.sessions[key]
            session.touched = time.monotonic()
            return session


class Laboratory:
    def __init__(self, root):
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='traffic-lab')
        self.jobs = {}
        self.lock = RLock()

    def submit(self, spec):
        with self.lock:
            self.cleanup()
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise HTTPException(409, 'Эксперимент уже выполняется. Дождитесь завершения.')
            job_id = uuid4().hex
            folder = self.root / (datetime.now().strftime('web_%Y%m%d_%H%M%S_') + job_id[:8])
            scenarios = list(SCENARIO_PROFILES) if spec.scenario == 'ALL' else [spec.scenario]
            self.jobs[job_id] = dict(id=job_id, status='queued', done=0, total=len(scenarios)*spec.runs*len(spec.controllers),
                                     folder=str(folder), created=time.monotonic())
            self.executor.submit(self.run, job_id, spec, scenarios, folder)
            return dict(self.jobs[job_id])

    def cleanup(self):
        # CSV stays on disk. Only finished in-memory handles expire.
        with self.lock:
            completed = [k for k,v in self.jobs.items() if v['status'] in ('complete', 'error')]
            for key in completed:
                if time.monotonic()-self.jobs[key]['created'] > 7200 or len(self.jobs)>16:
                    del self.jobs[key]

    def run(self, key, spec, scenarios, folder):
        try:
            with self.lock:
                self.jobs[key]['status'] = 'running'
            def progress(metrics, diagnostics):
                with self.lock:
                    self.jobs[key]['done'] += 1
            path = run_experiments(spec.config(), scenarios, list(range(spec.seed, spec.seed+spec.runs)), folder,
                                   controller_names=tuple(spec.controllers), custom_rates=spec.custom_rates,
                                   pedestrian_times=tuple(spec.pedestrian_times), progress=progress)
            rows = load_results(path)
            if len(spec.controllers)>1:
                rows, exclusions = select_complete_pairs(rows)
            else:
                exclusions = [f"{r['scenario']} seed={r['seed']}: incomplete" for r in rows if not r['completed_successfully']]
                rows = [r for r in rows if r['completed_successfully']]
            analysis = folder / 'analysis'; analysis.mkdir()
            if rows:
                summary, legacy = summarize(rows)
                write_table(analysis/'summary.csv', summary)
                if legacy:
                    write_table(analysis/'comparison.csv', legacy)
                comparisons = compare_all_pairs(rows)
                if comparisons:
                    write_table(analysis/'pairwise_comparison.csv', comparisons)
            (analysis/'analysis_report.txt').write_text('\n'.join(exclusions) or 'All runs complete. Equal weight per seed.\n')
            metrics = aggregate(read_csv(path), read_csv(folder/'diagnostics.csv'))
            with self.lock:
                self.jobs[key].update(status='complete', summary=metrics, exclusions=exclusions,
                                      completed_runs=len(rows), downloads={n:f'/api/experiments/{key}/files/{n}' for n in ('results.csv','diagnostics.csv','metadata.json')})
        except Exception as error:
            with self.lock:
                self.jobs[key].update(status='error', error=f'{type(error).__name__}: {error}')

    def get(self, key):
        with self.lock:
            if key not in self.jobs:
                raise HTTPException(404, 'Эксперимент не найден или истёк срок хранения в памяти')
            return dict(self.jobs[key])


def create_app(results_root: Path | None = None, *, testing=False):
    store = Store()
    lab = Laboratory(results_root or ROOT/'results')

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                store.cleanup(); lab.cleanup()
        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        lab.executor.shutdown(wait=True)

    # Disable Swagger/ReDoc pages that otherwise load their assets from a CDN.
    app = FastAPI(title='AdaptiveTraffic', docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store, app.state.lab = store, lab
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost'] + (['testserver'] if testing else []))

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        if request.method not in ('GET','HEAD'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
                return JSONResponse({'detail':'Разрешены запросы только с этой локальной страницы'}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.get('/')
    def index():
        return FileResponse(WEB/'index.html')

    app.mount('/static', StaticFiles(directory=WEB/'static'), name='static')

    @app.get('/api/project')
    def project():
        return dict(name='AdaptiveTraffic', controllers=list(CONTROLLERS), defaults=asdict(Config()),
                    settings=list(SETTINGS), github='https://github.com/durovp/AdaptiveTraffic')

    @app.get('/api/scenarios')
    def scenarios():
        return [dict(name=k, description=v) for k,v in SCENARIO_DESCRIPTIONS.items()]

    @app.get('/api/reference-results')
    def reference():
        return json.loads((WEB/'data/reference_results.json').read_text())

    @app.post('/api/sessions')
    def create_session(spec: SessionSpec):
        return store.add(Session(spec)).view()

    @app.get('/api/sessions/{key}')
    def get_session(key: str):
        session = store.get(key)
        with session.lock:
            return session.view()

    @app.delete('/api/sessions/{key}')
    def delete_session(key: str):
        with store.lock:
            store.sessions.pop(key, None)
        return {'deleted': True}

    @app.post('/api/sessions/{key}/advance')
    def advance(key: str, body: Advance):
        session = store.get(key)
        with session.lock:
            session.advance(body.steps)
            return session.view()

    @app.post('/api/sessions/{key}/finish')
    def finish(key: str):
        session = store.get(key)
        with session.lock:
            session.advance(round((session.plan.duration+session.config.drain_timeout)/session.config.time_step)+1)
            return session.view()

    @app.post('/api/sessions/{key}/restart')
    def restart(key: str, body: Restart):
        session = store.get(key)
        with session.lock:
            values = session.spec.model_dump()
            if body.controllers is not None:
                values['controllers'] = body.controllers
            try:
                session.spec = SessionSpec(**values)
            except ValueError as error:
                raise HTTPException(422, str(error))
            session.reset()
            return session.view()

    @app.post('/api/sessions/{key}/inject')
    def inject(key: str, body: Injection):
        session = store.get(key)
        with session.lock:
            session.event('inject', body.model_dump(mode='json'))
            return session.view()

    @app.post('/api/sessions/{key}/pedestrian')
    def pedestrian(key: str):
        session = store.get(key)
        with session.lock:
            session.event('pedestrian', {})
            return session.view()

    @app.get('/api/sessions/{key}/export')
    def export(key: str):
        session = store.get(key)
        with session.lock:
            if not all(s.finished for s in session.simulations):
                raise HTTPException(409, 'Сначала завершите симуляцию')
            return JSONResponse(dict(**session.view(), events=session.events, config=asdict(session.config)),
                                headers={'Content-Disposition': f'attachment; filename="simulation_{key}.json"'})

    @app.post('/api/comparison')
    def comparison(body: ComparisonSpec):
        if body.source_id:
            source = store.get(body.source_id)
            with source.lock:
                values = source.spec.model_dump(); values['controllers'] = body.controllers
                session = Session(SessionSpec(**values), plan=source.plan)
                for event in source.events:
                    session.advance(round((event['time']-session.time)/session.config.time_step))
                    session.event(event['kind'], {k:v for k,v in event.items() if k not in ('time','kind')})
                session.advance(round((source.time-session.time)/session.config.time_step))
        else:
            session = Session(SessionSpec(**body.model_dump(exclude={'source_id'})))
        return store.add(session).view()

    @app.post('/api/experiments')
    def experiment(body: ExperimentSpec):
        return lab.submit(body)

    @app.get('/api/experiments/{key}')
    def experiment_status(key: str):
        return lab.get(key)

    @app.get('/api/experiments/{key}/files/{name}')
    def download(key: str, name: Literal['results.csv','diagnostics.csv','metadata.json']):
        job = lab.get(key)
        if job['status'] != 'complete':
            raise HTTPException(409, 'Дождитесь завершения эксперимента')
        return FileResponse(Path(job['folder'])/name, filename=name)

    return app


def run_web(port=8000, open_browser=True):
    import uvicorn
    if open_browser:
        import webbrowser
        timer = Timer(1.0, lambda: webbrowser.open(f'http://127.0.0.1:{port}'))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(), host='127.0.0.1', port=port, log_level='info')
    return 0
