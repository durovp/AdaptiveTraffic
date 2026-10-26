'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const labels = {
  FIXED: 'FIXED',
  QUEUE_ONLY: 'QUEUE ONLY',
  ADAPTIVE: 'ADAPTIVE V1',
  ADAPTIVE_V2: 'ADAPTIVE V2'
};
const colors = {
  FIXED: '#8ebcff',
  QUEUE_ONLY: '#e4bb72',
  ADAPTIVE: '#c69ce9',
  ADAPTIVE_V2: '#76dfb7'
};
const metricLabels = {
  mean_wait: 'Среднее ожидание, с',
  p95_wait: 'P95 ожидания, с',
  mean_queue: 'Средняя очередь, авто',
  max_queue: 'Максимальная очередь, авто',
  total_clearing_time: 'Генерация + drain, с',
  number_of_switches: 'Переключения',
  transition_time_fraction: 'Доля переходов',
  average_green_duration: 'Средний зелёный, с'
};
const directions = ['NORTH', 'SOUTH', 'EAST', 'WEST'];
const pageNames = {
  home: 'Обзор',
  simulator: 'Симулятор',
  compare: 'Сравнение',
  laboratory: 'Лаборатория',
  results: 'Результаты',
  how: 'Как это работает',
  hardware: 'Физический макет',
  about: 'О проекте'
};
const sessions = {
  sim: null,
  compare: null
};
let hardware;
let project, scenarios, reference, page = 'home',
  job = null,
  pollTimer = null;
const escapeHTML = s => String(s).replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const n = (v, digits = 1) => v == null ? '—' : Number(v).toLocaleString('ru-RU', {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits
});

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.hidden = true, 7500);
}
async function api(path, body, method, extraHeaders = {}) {
  const r = await fetch(path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: {...(body === undefined ? {} : {'Content-Type':'application/json'}), ...extraHeaders},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!r.ok) {
    let data;
    try {
      data = await r.json();
    } catch {
      throw Error(`Ошибка сервера: ${r.status}`);
    }
    let detail = data.detail;
    if (Array.isArray(detail)) detail = detail.map(e => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('; ');
    throw Error(detail || `Ошибка ${r.status}`);
  }
  return r.json();
}

function listen(selector, event, fn) {
  $(selector).addEventListener(event, e => Promise.resolve().then(() => fn(e)).catch(e => toast(e.message)));
}

function modeForPage() {
  return page === 'compare' ? 'compare' : 'sim';
}

function navigate() {
  const hash = location.hash.slice(1);
  page = pageNames[hash] ? hash : 'home';
  for (const mode of Object.keys(sessions))
    if (sessions[mode] && !(hardware?.active && hardware.mode===mode)) sessions[mode].running = false;
  $$('.page').forEach(p => p.hidden = p.id !== page);
  $$('nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  $('#breadcrumb').textContent = `ADAPTIVETRAFFIC / ${pageNames[page].toUpperCase()}`;
  if (page === 'simulator' && !sessions.sim) createSession('sim').catch(e => toast(e.message));
  if (page === 'compare' && !sessions.compare) createSession('compare').catch(e => toast(e.message));
  Object.keys(sessions).forEach(updateStatus);
  window.scrollTo(0, 0);
}

function selectedControllers(prefix) {
  return $$(`#${prefix}-controllers input:checked`).map(x => x.value);
}

function readSpec(prefix) {
  return {
    scenario: $(`#${prefix}-scenario`).value,
    seed: Number($(`#${prefix}-seed`).value),
    duration: Number($(`#${prefix}-duration`).value),
    controllers: prefix === 'sim' ? [$('#sim-controller').value] : selectedControllers(prefix),
    custom_rates: $$(`#${prefix}-custom input`).map(x => Number(x.value)),
    interactive: prefix !== 'lab'
  };
}

function updateScenario(prefix) {
  const name = $(`#${prefix}-scenario`).value;
  $(`#${prefix}-custom`).hidden = name !== 'CUSTOM';
  $(`#${prefix}-description`).textContent = scenarios.find(x => x.name === name)?.description || 'Все семь стандартных сценариев на каждом seed.';
}

function updateStatus(mode) {
  const s = sessions[mode];
  if (!s) return;
  const status = $(`#${mode}-status`);
  status.textContent = s.working ? 'ВЫЧИСЛЕНИЕ…' : s.data.finished ? (s.data.views.every(v => v.completed_successfully) ? 'ЗАВЕРШЕНО' : 'ТАЙМ-АУТ') : s.running ? `ВЫПОЛНЯЕТСЯ / ×${s.speed}` : 'ПАУЗА';
  $$(`#${mode}-toolbar [data-speed]`).forEach(b => b.classList.toggle('selected', Number(b.dataset.speed) === s.speed));
}
async function createSession(mode, extra = {}, hardwareReset = false) {
  if (!hardwareReset && (hardware?.mode===mode || hardware?.binding)) await hardware.suspend('SAFE — новая симуляция');
  if (sessions[mode]) {
    sessions[mode].running = false;
    await sessions[mode].pending.catch(() => {});
  }
  const spec = {
    ...readSpec(mode),
    ...extra
  };
  const data = await api(mode === 'compare' ? '/api/comparison' : '/api/sessions', spec);
  const old = sessions[mode];
  sessions[mode] = {
    data,
    running: false,
    speed: 1,
    accumulator: 0,
    working: 0,
    pending: Promise.resolve()
  };
  if (old) api(`/api/sessions/${old.data.id}`, undefined, 'DELETE').catch(() => {});
  $(`#${mode}-event-status`).textContent = '';
  render(mode);
  return data;
}

function command(mode, suffix, body = {}, hardwareReset = false) {
  const s = sessions[mode];
  if (!s) return Promise.reject(Error('Сначала создайте симуляцию'));
  s.working++;
  updateStatus(mode);
  s.pending = s.pending.catch(() => {}).then(async () => {
    try {
      if (!hardwareReset && ['restart','finish'].includes(suffix) && (hardware?.mode===mode || hardware?.binding)) await hardware.suspend('SAFE — перезапуск / до конца');
      if (hardware?.active && hardware.mode===mode && suffix==='advance') body={steps:1, expected_revision:s.data.revision};
      const started=performance.now();
      s.data = await api(`/api/sessions/${s.data.id}/${suffix}`, body);
      await hardware?.snapshot(mode,s.data,started);
      if (s.data.finished) s.running = false;
      render(mode);
      return s.data;
    } finally {
      s.working--;
      updateStatus(mode);
    }
  });
  return s.pending;
}

function render(mode) {
  const s = sessions[mode],
    data = s.data,
    container = $(`#${mode}-cards`);
  if (container.dataset.session !== data.id || container.dataset.controllers !== data.views.map(v => v.controller).join(',')) {
    container.dataset.session = data.id;
    container.dataset.controllers = data.views.map(v => v.controller).join(',');
    container.innerHTML = data.views.map(v => `<article class="sim-card panel" data-controller="${v.controller}"><header><strong>${labels[v.controller]}</strong><span class="card-clock"></span></header><div class="card-body"><div class="canvas-wrap"><div class="signal-banner"></div><canvas width="600" height="500" aria-label="Перекрёсток ${labels[v.controller]}"></canvas></div><div class="live-panel"></div></div></article>`).join('');
  }
  data.views.forEach(v => {
    const card = $(`[data-controller="${v.controller}"]`, container);
    $('.card-clock', card).textContent = `${v.scenario} · seed ${v.seed} · ${n(v.time,0)} с`;
    $('.signal-banner', card).textContent = `${v.phase} · ${n(v.phase_elapsed,0)} с`;
    const debug = v.controller_debug;
    const rows = [
      ['N / S', `${v.queues.NORTH} / ${v.queues.SOUTH}`],
      ['E / W', `${v.queues.EAST} / ${v.queues.WEST}`],
      ['NS / EW очередь', `${v.queue_NS} / ${v.queue_EW}`],
      ['NS / EW ожидание', `${n(v.wait_NS)} / ${n(v.wait_EW)} с`],
      ['NS / EW приоритет', `${n(v.priorities.NS)} / ${n(v.priorities.EW)}`],
      ['Проехали / прибыли', `${v.cars_passed} / ${v.cars_generated}`],
      ['План / вручную', `${v.cars_planned} / ${v.manual_count}`],
      ['Переключений', v.number_of_switches],
      ['Решение', v.decision],
      ['Последняя причина', v.last_switch_reason]
    ];
    $('.live-panel', card).innerHTML = `<div class="metric-strip"><div class="metric-box"><small>ОЧЕРЕДЬ / АВТО</small><b>${v.queue_NS+v.queue_EW}</b></div><div class="metric-box"><small>ОЖИДАНИЕ</small><b>${n(v.mean_wait)} <em>с</em></b></div><div class="metric-box"><small>P95</small><b>${n(v.p95_wait)} <em>с</em></b></div></div><small>Ожидание и p95 — пока только обслуженные машины</small><div class="reason"><small>ПОЧЕМУ ТАКОЕ РЕШЕНИЕ</small>${escapeHTML(v.explanation)}</div><div class="live-data">${rows.map(([k,val])=>`<div><span>${k}</span><b>${val}</b></div>`).join('')}</div>${debug?`<div class="v2-data">balanced_load: ${debug.balanced_load_detected?'YES':'NO'}<br>ratio: ${n(debug.priority_ratio,2)} · effective_min: ${n(debug.effective_min_green,0)} с<br>priority current / other: ${n(debug.current_priority)} / ${n(debug.other_priority)}<br>Снимок решения: t=${n(debug.time,0)} с</div>`:''}<div class="ped-status">♙ ${v.pedestrian_signal==='GREEN'?'МОЖНО ИДТИ · GREEN':v.pedestrian_request?'ЗАПРОС ПРИНЯТ · RED':'СТОП · RED'} · ожидание ${n(v.pedestrian_wait,0)} с</div>`;
    const canvas = $('canvas', card);
    canvas._view = v;
    if (canvas._time !== v.time) {
      canvas._time = v.time;
      canvas._departures = v.departures;
      canvas._animationStart = performance.now();
    }
    drawIntersection(canvas, v);
  });
  const manualBlocked = data.finished || data.time >= data.spec.duration || !data.spec.interactive;
  const section = mode === 'sim' ? '#simulator' : '#compare';
  $$(`${section} [data-jam],${section} [data-inject],${section} [data-pedestrian]`).forEach(b => b.disabled = manualBlocked);
  $$(`#${mode}-toolbar [data-control="start"],#${mode}-toolbar [data-control="finish"]`).forEach(b => b.disabled = data.finished);
  const finished = $(`#${mode}-finished`);
  if (data.finished) {
    const rows = data.results.map(r => ({
      scenario: r.scenario,
      controller: r.controller,
      metrics: {
        ...r,
        ...r.diagnostics
      }
    }));
    finished.innerHTML = `<div class="panel"><h2>${data.views.every(v=>v.completed_successfully)?'Итог симуляции':'Достигнут тайм-аут'}</h2><p class="muted">${data.event_count?'С ручным демонстрационным вводом. Не научный headless-результат.':'Без ручных событий.'} ${data.views.every(v=>v.completed_successfully)?'Все автомобили и пешеходные запросы обслужены.':'Частичные ожидания нельзя использовать для вывода о преимуществе.'}</p><div class="downloads"><a href="/api/sessions/${data.id}/export">Скачать результат JSON ↓</a></div>${resultTable(rows)}</div>${chartGrid(rows,'mean_wait')}${comparisonText(data.comparisons)}`;
  } else finished.innerHTML = '';
  updateStatus(mode);
}

function comparisonText(rows) {
  const metric = rows.filter(r => r.metric === 'mean_wait');
  if (!metric.length) return '';
  return `<div class="panel">${metric.map(r=>`<p>${labels[r.controller]} относительно ${labels[r.baseline]}: ${r.improvement_percent===null?'процент не определён (нулевая база)':`${n(r.improvement_percent)}% улучшения среднего ожидания`}. Отрицательное значение — ухудшение.</p>`).join('')}</div>`;
}

function resultTable(rows) {
  const keys = ['mean_wait', 'p95_wait', 'mean_queue', 'max_queue', 'total_clearing_time', 'number_of_switches', 'transition_time_fraction', 'average_green_duration'];
  return `<div class="table-wrap"><table><thead><tr><th>Сценарий / алгоритм</th>${keys.map(k=>`<th>${metricLabels[k]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr><td>${r.scenario} / ${labels[r.controller]}</td>${keys.map(k=>`<td>${n(r.metrics[k],k==='transition_time_fraction'?3:2)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function chartGrid(rows, metric) {
  return `<div class="chart-grid">${[...new Set(rows.map(r=>r.scenario))].map(s=>{const records=rows.filter(r=>r.scenario===s),max=Math.max(0.001,...records.map(r=>r.metrics[metric]));return `<article class="panel chart-card"><h3>${s}</h3><svg viewBox="0 0 500 ${records.length*46+15}" role="img" aria-label="${metricLabels[metric]}: ${s}">${records.map((r,i)=>{const val=r.metrics[metric];return `<text x="0" y="${i*46+24}" fill="#becfd9" font-size="11">${labels[r.controller]}</text><rect x="120" y="${i*46+8}" width="${Math.max(0,val/max*300)}" height="24" rx="3" fill="${colors[r.controller]}"/><text x="${128+val/max*300}" y="${i*46+24}" fill="#eaf4f4" font-size="12">${n(val,metric==='transition_time_fraction'?3:1)}</text>`;}).join('')}</svg><p class="muted small">${metricLabels[metric]} · шкала начинается с нуля</p></article>`;}).join('')}</div>`;
}

function drawIntersection(canvas, view) {
  const c = canvas.getContext('2d'),
    w = 600,
    h = 500;
  c.clearRect(0, 0, w, h);
  c.fillStyle = '#14232b';
  c.fillRect(0, 0, w, h);
  c.strokeStyle = '#253640';
  c.lineWidth = 1;
  for (let x = 0; x < w; x += 30) {
    c.beginPath();
    c.moveTo(x, 0);
    c.lineTo(x, h);
    c.stroke();
  }
  for (let y = 0; y < h; y += 30) {
    c.beginPath();
    c.moveTo(0, y);
    c.lineTo(w, y);
    c.stroke();
  }
  c.fillStyle = '#2a3942';
  c.fillRect(235, 0, 130, h);
  c.fillRect(0, 185, w, 130);
  c.strokeStyle = '#a0acac';
  c.setLineDash([12, 13]);
  c.lineWidth = 2;
  for (const [x1, y1, x2, y2] of [
      [300, 0, 300, 172],
      [300, 330, 300, 500],
      [0, 250, 225, 250],
      [375, 250, 600, 250]
    ]) {
    c.beginPath();
    c.moveTo(x1, y1);
    c.lineTo(x2, y2);
    c.stroke();
  }
  c.setLineDash([]);
  c.fillStyle = '#b5c4bc';
  for (let x = 241; x < 361; x += 18) c.fillRect(x, 318, 10, 23);
  c.strokeStyle = '#dce6d7';
  c.lineWidth = 4;
  for (const [x1, y1, x2, y2] of [
      [240, 177, 295, 177],
      [305, 345, 360, 345],
      [223, 255, 223, 310],
      [377, 190, 377, 245]
    ]) {
    c.beginPath();
    c.moveTo(x1, y1);
    c.lineTo(x2, y2);
    c.stroke();
  }
  const positions = {
    NORTH: [270, 150, 0, 1],
    SOUTH: [330, 368, 0, -1],
    EAST: [405, 218, -1, 0],
    WEST: [195, 282, 1, 0]
  };

  function car(id, d, x, y) {
    const vertical = d === 'NORTH' || d === 'SOUTH';
    c.save();
    c.translate(x, y);
    c.fillStyle = ['#86b2d0', '#77c6ac', '#d7b87c', '#b5a1cc'][id % 4];
    c.beginPath();
    c.roundRect(vertical ? -9 : -14, vertical ? -14 : -9, vertical ? 18 : 28, vertical ? 28 : 18, 4);
    c.fill();
    c.fillStyle = '#304b5e';
    c.fillRect(vertical ? -6 : -4, vertical ? -6 : -6, vertical ? 12 : 8, 12);
    c.restore();
  }
  const qs = view?.queue_vehicles || {};
  for (const d of directions) {
    const [x, y, dx, dy] = positions[d];
    (qs[d] || []).slice(0, 5).forEach((id, i) => car(id, d, x - dx * i * 28, y - dy * i * 28));
  }
  const fraction = (performance.now() - (canvas._animationStart || 0)) / 600;
  if (fraction < 1 && view)
    for (const d of canvas._departures || []) {
      const [x, y, dx, dy] = positions[d.direction];
      car(d.id, d.direction, x + dx * 220 * fraction, y + dy * 220 * fraction);
    }
  const lightPositions = {
      NORTH: [205, 128],
      SOUTH: [381, 350],
      EAST: [383, 126],
      WEST: [181, 326]
    },
    palette = {
      RED: '#ef8b85',
      YELLOW: '#f1ce72',
      GREEN: '#76dfb7'
    };
  for (const d of directions) {
    const [x, y] = lightPositions[d], signal = view?.signals[d] || 'RED';
    c.fillStyle = '#0b171f';
    c.beginPath();
    c.roundRect(x, y, 22, 66, 5);
    c.fill();
    ['RED', 'YELLOW', 'GREEN'].forEach((s, i) => {
      c.fillStyle = signal === s ? palette[s] : '#35424a';
      c.beginPath();
      c.arc(x + 11, y + 12 + i * 21, 6, 0, Math.PI * 2);
      c.fill();
    });
  }
  c.font = '600 12px system-ui';
  c.textAlign = 'left';
  const names = {
    NORTH: [396, 58],
    SOUTH: [48, 415],
    EAST: [439, 354],
    WEST: [34, 127]
  };
  for (const d of directions) {
    const [x, y] = names[d];
    c.fillStyle = '#c4d4de';
    c.fillText(d, x, y);
    c.font = '11px system-ui';
    c.fillStyle = '#88a4b5';
    c.fillText(view ? `${view.queues[d]} авто${view.queues[d]>5?' · показано 5':''}` : 'Подход', x, y + 19);
    c.fillStyle = palette[view?.signals[d] || 'RED'];
    c.fillText(view?.signals[d] || 'RED', x, y + 37);
    c.font = '600 12px system-ui';
  }
  c.fillStyle = palette[view?.pedestrian_signal || 'RED'];
  c.beginPath();
  c.arc(404, 447, 8, 0, Math.PI * 2);
  c.fill();
  c.font = '12px system-ui';
  c.fillText(`ПЕШЕХОД · ${view?.pedestrian_signal||'RED'}`, 422, 451);
  c.fillStyle = '#93abba';
  c.font = '10px system-ui';
  c.textAlign = 'center';
  c.fillText('СХЕМА ОЧЕРЕДЕЙ · ДВИЖЕНИЕ ТОЛЬКО ПРЯМО', 300, 482);
}

function drawLoop() {
  for (const c of $$('.page:not([hidden]) .canvas-wrap canvas'))
    if (c._view && performance.now() - c._animationStart < 650) drawIntersection(c, c._view);
  requestAnimationFrame(drawLoop);
}
async function manual(kind, body) {
  const mode = modeForPage();
  const d = await command(mode, kind, body);
  $(`#${mode}-event-status`).textContent = kind === 'pedestrian' ? `Запрос передан в t=${n(d.time,0)} с. Повторный запрос во время пешеходного цикла объединяется.` : `Машины добавлены в t=${n(d.time,0)} с${mode==='compare'?' одинаково всем контроллерам':''}.`;
}
async function handleControl(control) {
  const mode = modeForPage(),
    s = sessions[mode];
  if (!s) return;
  if (control === 'start') {
    s.running = true;
    s.accumulator = 0;
    updateStatus(mode);
  }
  if (control === 'pause') {
    s.running = false;
    if (hardware?.mode===mode || hardware?.binding) await hardware.suspend('SAFE — пауза; для вывода снова синхронизируйте');
    updateStatus(mode);
  }
  if (control === 'restart') {
    s.running = false;
    s.accumulator = 0;
    await command(mode, 'restart', {});
  }
  if (control === 'finish') {
    s.running = false;
    s.accumulator = 0;
    await command(mode, 'finish', {});
    $(`#${mode}-finished`).scrollIntoView({
      behavior: 'smooth',
      block: 'start'
    });
  }
}

function showReference() {
  const cfg = reference.config;
  $('#reference-meta').innerHTML = `<div class="panel-heading"><strong>НЕЗАВИСИМАЯ ПРОВЕРКА / HOLDOUT</strong><span class="chip">${reference.total_runs-reference.incomplete_runs} / ${reference.total_runs} завершены</span></div><p>Seeds ${reference.seeds[0]}–${reference.seeds.at(-1)} · 7 сценариев · 4 контроллера · генерация ${cfg.simulation_duration} с</p><small>Создано: ${escapeHTML(reference.generated_at)}. Выбор на seeds 42–51 завершён до holdout.<br>V2: очередь ≥ ${cfg.v2_busy_queue_threshold}, ratio ≥ ${cfg.v2_balance_ratio}, минимум ${cfg.v2_balanced_min_green} с. Источник: ${escapeHTML(reference.source)}.</small>`;
  $('#reference-charts').innerHTML = chartGrid(reference.metrics, $('#reference-metric').value);
  $('#reference-table').innerHTML = `<div class="panel"><h2>Все показатели holdout</h2>${resultTable(reference.metrics)}</div>`;
}

function labSettings() {
  return Object.fromEntries($$('#advanced-settings input').map(x => [x.name, Number(x.value)]));
}
async function runLab(event) {
  if (event) event.preventDefault();
  const body = {
    ...readSpec('lab'),
    runs: Number($('#lab-runs').value),
    settings: labSettings(),
    pedestrian_times: $('#lab-pedestrians').value.trim() ? $('#lab-pedestrians').value.split(/[\s,;]+/).filter(Boolean).map(Number) : []
  };
  $('#run-lab').disabled = true;
  try {
    const token=$('#lab-admin-token').value.trim();
    job = await api('/api/experiments', body, undefined, token ? {Authorization:`Bearer ${token}`} : {});
    $('#lab-results').innerHTML = '';
    $('#lab-progress').hidden = false;
    $('#lab-error').textContent = '';
    await pollJob();
  } catch (e) {
    $('#run-lab').disabled = false;
    throw e;
  }
}
async function pollJob() {
  clearTimeout(pollTimer);
  try {
    job = await api(`/api/experiments/${job.id}`);
    $('#lab-progress-count').textContent = `${job.done} / ${job.total} запусков`;
    $('#lab-progress-bar').max = job.total;
    $('#lab-progress-bar').value = job.done;
    $('#lab-progress-label').textContent = {
      queued: 'В очереди',
      running: 'Эксперимент выполняется',
      complete: 'Эксперимент завершён',
      error: 'Ошибка эксперимента'
    } [job.status];
    if (job.status === 'complete') {
      $('#run-lab').disabled = false;
      $('#lab-results').innerHTML = `<div class="panel"><h2>Результаты лаборатории</h2><p>${job.completed_runs} завершённых запусков включено в сравнение. Исключённых групп: ${job.exclusions.length}.</p>${job.exclusions.length?`<p>${job.exclusions.map(escapeHTML).join('<br>')}</p>`:''}<div class="downloads">${Object.entries(job.downloads).map(([name,url])=>`<a href="${url}">Скачать ${name} ↓</a>`).join('')}</div><p class="muted small">Папка: ${escapeHTML(job.folder)}</p><label>График<select id="lab-metric">${Object.entries(metricLabels).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label></div><div id="lab-chart"></div><div class="panel">${resultTable(job.summary)}</div>`;
      $('#lab-chart').innerHTML = chartGrid(job.summary, 'mean_wait');
      listen('#lab-metric', 'change', () => $('#lab-chart').innerHTML = chartGrid(job.summary, $('#lab-metric').value));
    } else if (job.status === 'error') {
      $('#run-lab').disabled = false;
      $('#lab-error').textContent = job.error;
    } else pollTimer = setTimeout(() => pollJob(), 400);
  } catch (e) {
    $('#run-lab').disabled = false;
    $('#lab-error').textContent = e.message;
  }
}

function initializeHardware() {
  hardware=new HardwareDemo({
    notify:toast,
    pause:mode=> { if (sessions[mode]) { sessions[mode].running=false; updateStatus(mode); } },
    settled:mode=>sessions[mode]?.pending.catch(()=>{}),
    restart:async mode=> {
      if (!sessions[mode]) await createSession(mode,{},true);
      return command(mode,'restart',{},true);
    },
    current:async mode=> {
      const start=performance.now();
      const data=await api(`/api/sessions/${sessions[mode].data.id}`);
      if (performance.now()-start>1500) throw Error('Ответ Python слишком медленный');
      return data;
    },
    start:mode=> { const s=sessions[mode]; s.speed=1; s.accumulator=0; s.running=true; updateStatus(mode); },
    pedestrian:mode=>command(mode,'pedestrian',{})
  });
  $('#hw-support').textContent=window.isSecureContext && navigator.serial ?
    'Web Serial доступен. Подключение запрашивается только кнопкой ниже.' :
    'Web Serial недоступен: откройте HTTPS/localhost в Chrome или Chromium на компьютере. Симулятор работает без USB.';
  listen('#hw-connect','click',()=> {
    const timings=['yellow','all_red','ped_green','ped_clearance'].map(k=>project.defaults[k]*1000);
    if (JSON.stringify(timings)!==JSON.stringify([3000,1000,10000,3000])) throw Error('Интервалы Python не соответствуют прошивке Protocol 1');
    return hardware.serial.connect();
  });
  listen('#hw-disconnect','click',()=>hardware.disconnect());
  listen('#hw-stop','click',()=>hardware.suspend('SAFE — безопасное отключение'));
  listen('#hw-check','click',()=>hardware.serial.command('PING'));
  listen('#hw-pedestrian','click',()=>hardware.button());
  listen('#hw-bind','click',()=>hardware.bind($('#hw-source').value,$('#hw-controller').value));
  for (const id of ['hw-source','hw-controller']) listen('#'+id,'change',()=>hardware.suspend('SAFE — выбор изменён; нужна синхронизация'));
  listen('#hw-test','click',async()=> {
    if (!hardware.serial.connected) throw Error('Сначала подключите Arduino');
    await hardware.suspend('SAFE — подготовка теста LED');
    $('#sim-controller').value='FIXED'; $('#sim-scenario').value='BALANCED_LOW'; $('#sim-duration').value=80;
    await createSession('sim',{controllers:['FIXED'],scenario:'BALANCED_LOW',duration:80,pedestrian_times:[5]});
    $('#hw-source').value='sim'; $('#hw-controller').value='FIXED';
    await hardware.bind('sim','FIXED');
    toast('Тест: обычная Python-модель FIXED, 80 с генерации + обслуживание. Проверьте все 14 LED.');
  });
}

async function initialize() {
  [project, scenarios, reference] = await Promise.all([api('/api/project'), api('/api/scenarios'), api('/api/reference-results')]);
  initializeHardware();
  $('#lab-auth').hidden=!project.hosting.production;
  if (project.hosting.production) for (const mode of ['sim','compare']) $(`#${mode}-duration`).max=project.hosting.max_duration;
  for (const prefix of ['sim', 'compare', 'lab']) {
    $(`#${prefix}-scenario`).innerHTML = (prefix === 'lab' ? '<option value="ALL">Все 7 сценариев</option>' : '') + scenarios.map(s => `<option value="${s.name}">${s.name.replaceAll('_',' ')}</option>`).join('');
    $(`#${prefix}-scenario`).value = prefix === 'lab' ? 'ALL' : 'NS_HEAVY';
    $(`#${prefix}-custom`).innerHTML = directions.map((d, i) => `<label>${d} · <output>${[12,12,4,4][i]}</output> авто/мин<input aria-label="${d} авто в минуту" type="range" min="0" max="25" step="0.5" value="${[12,12,4,4][i]}"></label>`).join('');
    $$(`#${prefix}-custom input`).forEach(x => x.addEventListener('input', () => $('output', x.parentElement).textContent = x.value));
    listen(`#${prefix}-scenario`, 'change', () => updateScenario(prefix));
    updateScenario(prefix);
    if (prefix !== 'sim') $(`#${prefix}-controllers`).innerHTML = project.controllers.map(c => `<label><input type="checkbox" value="${c}" ${prefix==='lab'||['FIXED','ADAPTIVE_V2'].includes(c)?'checked':''}>${labels[c]}</label>`).join('');
  }
  $('#sim-controller').innerHTML = project.controllers.map(c => `<option value="${c}">${labels[c]}</option>`).join('');
  $('#sim-controller').value = 'ADAPTIVE_V2';
  for (const prefix of ['sim', 'compare']) $(`#${prefix}-injections`).innerHTML = directions.map(d => `<div><span>${d}</span>${[1,5,10].map(count=>`<button data-inject="${d}" data-count="${count}" aria-label="Добавить ${count} ${d}">+${count}</button>`).join('')}</div>`).join('');
  $('#advanced-settings').innerHTML = project.settings.map(k => `<label>${k.toUpperCase()}<input type="number" name="${k}" min="${k==='switch_margin'?0:k==='v2_balance_ratio'?0.01:1}" max="${k==='v2_balance_ratio'?1:600}" step="${['switch_margin','wait_equivalent','v2_busy_queue_threshold','v2_balance_ratio'].includes(k)?0.01:1}" value="${project.defaults[k]}"></label>`).join('');
  $('#reference-metric').innerHTML = Object.entries(metricLabels).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
  $('#wait-equivalent').textContent = project.defaults.wait_equivalent;
  $('#v2-formula').textContent = `both queues ≥ ${project.defaults.v2_busy_queue_threshold}\nmin(priority) / max(priority) ≥ ${project.defaults.v2_balance_ratio}\n→ effective_min_green = ${project.defaults.v2_balanced_min_green} с`;
  $('#v2-formula').style.whiteSpace = 'pre-line';
  $('#parameter-explanations').innerHTML = [
    ['min_green', 'Минимальная длительность зелёного'],
    ['max_green', 'Максимум при спросе другой стороны'],
    ['max_wait', 'Порог защиты долго ожидающей стороны'],
    ['switch_margin', 'Необходимый перевес приоритета']
  ].map(([k, v]) => `<div><h3>${k.toUpperCase()} = ${project.defaults[k]}</h3><p>${v}</p></div>`).join('');
  listen('#sim-form', 'submit', e => {
    e.preventDefault();
    return createSession('sim');
  });
  listen('#compare-form', 'submit', e => {
    e.preventDefault();
    return createSession('compare');
  });
  listen('#sim-controller', 'change', async () => {
    if (sessions.sim) {
      sessions.sim.running = false;
      await command('sim', 'restart', {
        controllers: [$('#sim-controller').value]
      });
    }
  });
  listen('#try-demo', 'click', async () => {
    $('#sim-scenario').value = 'NS_HEAVY';
    $('#sim-controller').value = 'ADAPTIVE_V2';
    $('#sim-seed').value = 42;
    $('#sim-duration').value = 180;
    updateScenario('sim');
    await createSession('sim');
    location.hash = 'simulator';
    setTimeout(() => {
      sessions.sim.running = true;
      updateStatus('sim');
      $('#sim-toolbar').scrollIntoView({
        block: 'start'
      });
    }, 0);
  });
  listen('#home-compare', 'click', () => {
    location.hash = 'compare';
  });
  listen('#compare-current', 'click', async () => {
    const source = sessions.sim;
    source.running = false;
    await source.pending;
    const spec = source.data.spec;
    $('#compare-scenario').value = spec.scenario;
    $('#compare-seed').value = spec.seed;
    $('#compare-duration').value = spec.duration;
    $$('#compare-custom input').forEach((x, i) => {
      x.value = spec.custom_rates[i];
      $('output', x.parentElement).textContent = x.value;
    });
    $$('#compare-controllers input').forEach(x => x.checked = ['FIXED', 'ADAPTIVE_V2'].includes(x.value));
    updateScenario('compare');
    await createSession('compare', {
      source_id: source.data.id,
      controllers: ['FIXED', 'ADAPTIVE_V2']
    });
    location.hash = 'compare';
  });
  document.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    let task;
    if (b.dataset.control) task = handleControl(b.dataset.control);
    if (b.dataset.speed) {
      const s = sessions[modeForPage()];
      if (s) {
        if ((hardware?.active || hardware?.binding) && Number(b.dataset.speed)!==1) {
          toast('HARDWARE DEMO MODE: доступна только ×1. Сначала безопасно отключите аппаратный вывод.'); return;
        }
        s.speed = Number(b.dataset.speed);
        updateStatus(modeForPage());
      }
    }
    if (b.dataset.jam) task = manual('inject', {
      directions: b.dataset.jam === 'NS' ? ['NORTH', 'SOUTH'] : ['EAST', 'WEST'],
      count: 10
    });
    if (b.dataset.inject) task = manual('inject', {
      directions: [b.dataset.inject],
      count: Number(b.dataset.count)
    });
    if (b.hasAttribute('data-pedestrian')) task = manual('pedestrian', {});
    if (task) task.catch(e => toast(e.message));
  });
  listen('#lab-form', 'submit', runLab);
  listen('#reset-settings', 'click', () => $$('#advanced-settings input').forEach(x => x.value = project.defaults[x.name]));
  listen('#reference-metric', 'change', showReference);
  listen('#reproduce', 'click', async () => {
    $('#lab-scenario').value = 'ALL';
    $('#lab-runs').value = reference.seeds.length;
    $('#lab-seed').value = reference.seeds[0];
    $('#lab-duration').value = reference.config.simulation_duration;
    $('#lab-pedestrians').value = '';
    $$('#lab-controllers input').forEach(x => x.checked = reference.controllers.includes(x.value));
    $$('#advanced-settings input').forEach(x => x.value = reference.config[x.name]);
    updateScenario('lab');
    location.hash = 'laboratory';
    await runLab();
  });
  for (const id of ['calc-queue', 'calc-wait']) listen(`#${id}`, 'input', () => {
    $('#calc-queue-value').textContent = $('#calc-queue').value;
    $('#calc-wait-value').textContent = $('#calc-wait').value;
    $('#calc-result').textContent = n(Number($('#calc-queue').value) + Number($('#calc-wait').value) / project.defaults.wait_equivalent, 2);
  });
  listen('#stand', 'click', async () => {
    const enabled = document.body.classList.toggle('stand');
    $('#stand').textContent = enabled ? '⛶ Выйти из стенда' : '⛶ Режим стенда';
    try {
      if (enabled && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      else if (!enabled && document.fullscreenElement) await document.exitFullscreen();
    } catch {
      /* CSS stand mode also works without Fullscreen API. */ }
  });
  window.addEventListener('hashchange', navigate);
  showReference();
  drawIntersection($('#hero-canvas'), null);
  navigate();
  drawLoop();
  let last = performance.now();
  setInterval(() => {
    const now = performance.now(),
      elapsed = Math.min((now - last) / 1000, 1);
    last = now;
    for (const mode of ['sim', 'compare']) {
      const s = sessions[mode];
      if (!s?.running || s.data.finished) continue;
      if (hardware?.binding) continue;
      if (hardware?.active && hardware.mode===mode) {
        if (!s.working && hardware.canStep(mode)) command(mode,'advance',{steps:1}).catch(async e=> {
          s.running=false; await hardware.suspend('DESYNC — '+e.message).catch(()=>{}); toast(e.message);
        });
        continue;
      }
      s.accumulator += elapsed * s.speed;
      if (s.working || s.accumulator < 1) continue;
      const steps = Math.min(Math.floor(s.accumulator), 1000);
      s.accumulator -= steps;
      command(mode, 'advance', {
        steps
      }).catch(e => {
        s.running = false;
        updateStatus(mode);
        toast(e.message);
      });
    }
  }, 100);
}
initialize().catch(e => toast(`Не удалось открыть лабораторию: ${e.message}. Проверьте, что локальный сервер запущен.`));
