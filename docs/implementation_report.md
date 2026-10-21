# Отчёт этапа AdaptiveTraffic — 2026-10-12

Работа выполнена в существующем проекте. Исходная точка: main, 9633b41.
Начальные тесты: 152 passed, чистая рабочая папка. Commit/push не выполнялись.

## 1. Добавленные файлы

```text
adaptive_traffic/research.py
adaptive_traffic/web.py
docs/adaptive_v2_research.md
docs/implementation_report.md
docs/v2_selection.json
docs/web_api.md
tests/fixtures/legacy_baseline.json
tests/test_adaptive_v2.py
tests/test_legacy_regression.py
tests/test_web.py
web/data/reference_results.json
web/index.html
web/static/app.js
web/static/icon.svg
web/static/style.css
```

## 2. Изменённые файлы

```text
README.md
adaptive_traffic/analyze.py
adaptive_traffic/config.py
adaptive_traffic/controllers.py
adaptive_traffic/experiments.py
adaptive_traffic/gui.py
adaptive_traffic/presentation.py
adaptive_traffic/scenarios.py
adaptive_traffic/simulation.py
docs/algorithm.md
docs/simulation_model.md
requirements.txt
tests/test_analysis.py
tests/test_diagnostics.py
tests/test_queue_only.py
```

## 3. Проверки

- pytest: **231 passed**, 1 предупреждение зависимости Starlette об устаревании
  httpx в TestClient (не ошибка теста).
- Побайтно совпали results.csv, diagnostics.csv и все 210 журналов старых
  FIXED/QUEUE_ONLY/ADAPTIVE относительно baseline 42–51, все 7 сценариев.
- В браузере Chromium проверены главная, API/static, demo, очередь, пешеход,
  сравнение с повтором событий, CUSTOM, 4 контроллера, ×100, fast forward,
  лаборатория 10 seeds, экспорт, калькулятор и стенд. Ошибок JS нет.
- Сетевой перехват не обнаружил внешних запросов; всё загружается локально.
- Кнопка воспроизведения выполнила 560 запусков: CSV и сводка точно совпали
  с исходным holdout. Параметры не перенастраивались.
- Размеры браузера: 1366×768 и 1920×1080. Артефакты: `results/web_smoke_20261012/`.
- Pygame V2: успешный smoke на SDL dummy, 2 кадра после 45 секунд модели;
  локальная сборка выдаёт прежнее предупреждение об отсутствии AVX2-оптимизации.
- JavaScript: синтаксическая проверка прошла; git diff --check прошёл.

## 4. Baseline V1

Все 7 сценариев × seeds 42–51 × 3 контроллера = 210 успешных запусков.
BALANCED_HIGH: mean_wait V1 127.826 с, FIXED 32.991 с.
RUSH_HOUR: V1 239.568 с, FIXED 139.475 с.
CSV и анализ: `results/v2_baseline_20261012/`.

## 5. Что показала диагностика

BALANCED_HIGH V1: зелёный 10.348 с, доля переходов 0.277, 145.9 смен в среднем;
из них 125.3 MAX_WAIT, 18.5 HIGHER_PRIORITY, 0 MAX_GREEN.
RUSH_HOUR V1: зелёный 10.366 с, переходы 0.277, 171.6 смен;
из них 100.8 MAX_WAIT, 23.5 HIGHER_PRIORITY, 0 MAX_GREEN.
Преобладают смены после MIN_GREEN из-за накопившегося ожидания, усиливающие
потерю обслуживания. Это поддерживает гипотезу, а не доказывает единственную
причину. Вторая половина RUSH_HOUR также физически перегружена в данной модели.

## 6. Точная логика V2

Приоритеты = queue + oldest_wait / WAIT_EQUIVALENT. Проверить both_busy по
обеим очередям; ratio = min(priority)/max(priority), при нулевом знаменателе 0.
Если both_busy и ratio достаточен, effective_min = BALANCED_MIN_GREEN,
иначе MIN_GREEN. Значение ограничивается [MIN_GREEN, MAX_GREEN].
До effective_min KEEP; после — EMPTY_CURRENT, MAX_WAIT, MAX_GREEN,
строгое other_priority > current_priority + SWITCH_MARGIN, иначе KEEP.
Условие пересчитывается каждый шаг. Пустая очередь или сильная асимметрия
отключают стабилизацию. Пешеходный timeout после обычного MIN_GREEN сохранён.
Сценарий не передаётся в алгоритм. V1 остаётся отдельным неизменённым классом.

## 7. Выбранные параметры

BUSY_QUEUE_THRESHOLD = **8**, BALANCE_RATIO = **0.65**,
BALANCED_MIN_GREEN = **30 с**. Прочие параметры прежние.
Ровно 18 кандидатов на seeds 42–51. Выбранный прошёл development guardrails.
Выбор сохранён до holdout в `docs/v2_selection.json` и полном selection.json
в development-папке. После holdout изменений настройки и правил V2 не было.

## 8. Development results

Среднее ожидание, секунды. Положительный процент — улучшение.

| Сценарий | FIXED | QUEUE_ONLY | V1 | V2 | V2 vs V1 | V2 vs FIXED |
|---|---:|---:|---:|---:|---:|---:|
| BALANCED_HIGH | 32.991 | 127.891 | 127.826 | 46.362 | +63.73% | -40.53% |
| BALANCED_LOW | 15.499 | 8.767 | 8.976 | 8.976 | +0.00% | +42.09% |
| CHANGING_FLOW | 99.444 | 19.140 | 19.782 | 19.753 | +0.15% | +80.14% |
| EW_HEAVY | 409.296 | 41.668 | 44.836 | 44.828 | +0.02% | +89.05% |
| MINOR_ROAD | 332.926 | 10.329 | 10.497 | 10.497 | +0.00% | +96.85% |
| NS_HEAVY | 396.435 | 39.056 | 39.155 | 39.122 | +0.08% | +90.13% |
| RUSH_HOUR | 139.475 | 239.007 | 239.568 | 142.571 | +40.49% | -2.22% |


## 9. Holdout results

Seeds 100–119, 560/560 успешны; ни один seed не исключён.

| Сценарий | FIXED | QUEUE_ONLY | V1 | V2 | V2 vs V1 | V2 vs FIXED |
|---|---:|---:|---:|---:|---:|---:|
| BALANCED_HIGH | 33.246 | 125.431 | 126.296 | 45.001 | +64.37% | -35.36% |
| BALANCED_LOW | 14.771 | 8.599 | 8.839 | 8.839 | +0.00% | +40.16% |
| CHANGING_FLOW | 93.955 | 18.256 | 19.524 | 19.390 | +0.69% | +79.36% |
| EW_HEAVY | 393.698 | 37.690 | 38.189 | 37.841 | +0.91% | +90.39% |
| MINOR_ROAD | 330.676 | 9.982 | 10.159 | 10.159 | +0.00% | +96.93% |
| NS_HEAVY | 394.240 | 40.727 | 40.869 | 40.802 | +0.16% | +89.65% |
| RUSH_HOUR | 137.979 | 238.343 | 238.971 | 142.178 | +40.50% | -3.04% |

P95, средний зелёный, переходы и число смен для всех контроллеров:
[полная таблица исследования](adaptive_v2_research.md).

## 10. BALANCED_HIGH отдельно

Holdout mean_wait: FIXED **33.246 с**, V1 **126.296 с**, V2 **45.001 с**.
V2 улучшил V1 на 64.37%, но уступил FIXED на 35.36%.
Переходы V1/V2: 0.2784 / 0.1925. Средний зелёный: 10.299 / 16.775 с.

## 11. RUSH_HOUR отдельно

Holdout mean_wait: FIXED **137.979 с**, V1 **238.971 с**, V2 **142.178 с**.
V2 улучшил V1 на 40.50%, но уступил FIXED на 3.04%.
Переходы V1/V2: 0.2789 / 0.1919. Средний зелёный: 10.285 / 16.696 с.

## 12. Остальные сценарии

Среднее не ухудшилось. NS_HEAVY: −0.16%, EW_HEAVY: −0.91%, CHANGING_FLOW:
−0.69%. BALANCED_LOW и MINOR_ROAD совпали. P95 также не вырос.
Небольшие различия не являются доказательством статистически значимого выигрыша.

## 13. Запуск сайта

```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py --web
```

Адрес: http://127.0.0.1:8000. Другой порт: `--web-port 8080`.
Без автооткрытия: `--no-browser`. Тестовый сервер остановлен; порт освобождён.

## 14–17. Путь для жюри

Главная → **Попробовать самим** → **Пробка NS** (по 10 на NORTH/SOUTH)
→ **Пешеходный запрос** → **Сравнить с FIXED** → **До конца**.
Результат — таблица, график и JSON. Все уже введённые события повторяются
для сравниваемых контроллеров в исходные модельные моменты.

## 18. Все четыре алгоритма

В разделе «Сравнение» включить все четыре флажка и нажать «Начать сравнение».
Headless:

```bash
python main.py --controller all --scenario all --runs 10 --seed 42 --output results/my_four
```

## 19. Эксперимент на 10 seeds

Лаборатория → сценарий → контроллеры → число seeds **10**, первый seed **42**,
длительность → **Запустить эксперимент**. Это seeds 42–51; ручного ввода нет.
Работают progress bar и скачивание настоящих CSV.

## 20. CSV и графики

- Baseline: `results/v2_baseline_20261012/`.
- Все 18 вариантов: `results/v2_development_20261012/q*_r*_g*/`.
- Выбранный development с 4 алгоритмами и analysis:
  `results/v2_development_20261012/selected_comparison/`.
- Holdout: `results/v2_holdout_20261012/`.
- Проверка старых алгоритмов: `results/v2_legacy_regression_20261012/`.
- Лаборатория: отдельная `results/web_<timestamp>_<id>/` для каждого опыта.

Везде results.csv и diagnostics.csv; headless/lab также metadata и журналы.
Большие generated results остаются локально и игнорируются Git.

## 21. Reference results

[web/data/reference_results.json](../web/data/reference_results.json) —
подготовленная сводка реального holdout, около 28 КБ: seeds, config, сценарии,
контроллеры, метрики, дата, версия Python, SHA-256 CSV и development selection.

## 22. Оставшиеся ограничения

Локальная модель одного пользователя, без общей базы: 32 сессии, TTL 30 минут,
один фоновый эксперимент. После перезапуска сервера сессии исчезают, CSV остаются.
Ручной ввод ограничен периодом генерации и 1000 машинами. На рисунке максимум
5 автомобилей на подход; численные очереди точные. Нет реальной физики движения,
поворотов, соседних перекрёстков и измеренных потоков. Нет доверительных интервалов
в данной сводке. Серверные каталоги показываются текстом; браузер не запускает
файловый менеджер. В библиотеке TestClient есть предупреждение deprecation.

## 23. git diff --stat

Точный вывод ниже учитывает отслеживаемые файлы; новые перечислены в пункте 1
и ещё не добавлены в индекс.

```text
 README.md                        | 543 ++++++++++++++-------------------------
 adaptive_traffic/analyze.py      |  19 +-
 adaptive_traffic/config.py       |   6 +
 adaptive_traffic/controllers.py  |  45 +++-
 adaptive_traffic/experiments.py  |  35 ++-
 adaptive_traffic/gui.py          |   9 +-
 adaptive_traffic/presentation.py |  32 ++-
 adaptive_traffic/scenarios.py    |   5 +
 adaptive_traffic/simulation.py   |  49 +++-
 docs/algorithm.md                |  30 ++-
 docs/simulation_model.md         |  17 ++
 requirements.txt                 |   3 +
 tests/test_analysis.py           |   4 +-
 tests/test_diagnostics.py        |   2 +-
 tests/test_queue_only.py         |   2 +-
 15 files changed, 412 insertions(+), 389 deletions(-)
```

## 24. git status

```text
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   README.md
	modified:   adaptive_traffic/analyze.py
	modified:   adaptive_traffic/config.py
	modified:   adaptive_traffic/controllers.py
	modified:   adaptive_traffic/experiments.py
	modified:   adaptive_traffic/gui.py
	modified:   adaptive_traffic/presentation.py
	modified:   adaptive_traffic/scenarios.py
	modified:   adaptive_traffic/simulation.py
	modified:   docs/algorithm.md
	modified:   docs/simulation_model.md
	modified:   requirements.txt
	modified:   tests/test_analysis.py
	modified:   tests/test_diagnostics.py
	modified:   tests/test_queue_only.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	adaptive_traffic/research.py
	adaptive_traffic/web.py
	docs/adaptive_v2_research.md
	docs/implementation_report.md
	docs/v2_selection.json
	docs/web_api.md
	tests/fixtures/
	tests/test_adaptive_v2.py
	tests/test_legacy_regression.py
	tests/test_web.py
	web/

no changes added to commit (use "git add" and/or "git commit -a")
```

Commit и push не выполнены. Ветка main и HEAD 9633b41 сохранены для ручной проверки.
