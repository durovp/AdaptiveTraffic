# AdaptiveTraffic V3 на Linux VPS

Инструкция для администратора собственного VPS. Сервер не арендован и не
настроен автоматически. Выберите Linux VPS с Docker/Compose, HTTPS reverse
proxy, минимум 512 МБ RAM (1 ГБ удобнее для ОС и сборки). Цена 150–300 ₽ может
не включать домен/IPv4; проверьте полную стоимость перед оплатой.

## Загрузка и конфигурация

Установите Docker Engine с Compose plugin по официальной инструкции для
дистрибутива. Скачайте существующий репозиторий после отдельного разрешения
на публикацию V3 или перенесите подготовленные исходники. На VPS:

```bash
git clone https://github.com/durovp/AdaptiveTraffic.git
cd AdaptiveTraffic
```

GitHub сейчас может ещё не содержать локальные V3-файлы: эта работа не делает
commit/push. Проверьте наличие Dockerfile и deploy/compose.yaml до сборки.
Не переносите виртуальное окружение, временные результаты и секреты компьютера.

Создайте в корне `.env` с правами 600. Он исключён из Git и Docker build context:

```dotenv
TRAFFIC_ENV=production
HOST=0.0.0.0
PORT=8000
ALLOWED_HOSTS=traffic.example.org,127.0.0.1,localhost
ALLOWED_ORIGINS=https://traffic.example.org
RESULTS_DIR=/data/results
MAX_SESSIONS=8
SESSION_TTL=900
MAX_DURATION=600
FORWARDED_ALLOW_IPS=127.0.0.1
```

Замените пример на свой домен. Добавьте ADMIN_TOKEN, сгенерированный
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. Не вставляйте
настоящий ключ в историю Git, URL, инструкции или скриншоты.

```bash
chmod 600 .env
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml logs --tail=100
curl -f http://127.0.0.1:8000/healthz
```

Compose использует один контейнер: максимум 448 МиБ RAM, 0,25 CPU, 64 PID,
read-only rootfs, отдельный `/tmp`, именованный том результатов, непривилегированный
UID 10001, без capabilities. На машинах с 512 МБ необходимо отдельно оставить
память ОС/proxy и проверить реальные пики; не считаем этот размер гарантией.
Не масштабируйте сервис в несколько workers/реплик без общего хранилища сессий.
Веб-контейнер использует Python 3.12 и requirements-web.txt; научная среда
локально устанавливается из полного requirements.txt.

## HTTPS reverse proxy

Направьте DNS A/AAAA своего домена на VPS. Откройте 80/443, оставьте 8000
привязанным к 127.0.0.1. Установите Caddy по официальной инструкции для ОС.
Пример `/etc/caddy/Caddyfile`:

```caddyfile
traffic.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

После замены домена проверьте конфигурацию `caddy validate --config
/etc/caddy/Caddyfile` и перезагрузите Caddy. При корректном DNS и доступных
портах Caddy получает и обновляет сертификат автоматически. Обязательно
проверьте настоящий HTTPS-адрес в браузере.
[Документация Caddy: automatic HTTPS](https://caddyserver.com/docs/automatic-https)

Uvicorn получает трафик из Docker bridge. Если хотите корректный клиентский IP,
выясните адрес gateway созданной Docker-сети через `docker network inspect`
и добавьте **только этот доверенный адрес** в FORWARDED_ALLOW_IPS. Сеть не
должна содержать чужие контейнеры; backend-порт не должен быть публичным.
До этого rate limiter может учитывать все запросы proxy как одного клиента,
что ограничивает доступ строже. Не используйте wildcard доверия.

Проверка Origin опирается на точный ALLOWED_ORIGINS и не требует слепо доверять
X-Forwarded-Proto. CORS не открыт. Для Postman/curl POST передавайте правильный
`Origin: https://ваш-домен`; лаборатории также нужен Authorization Bearer.
В штатном браузере Origin устанавливается автоматически.

## Проверки, USB и обновление

1. GET `/healthz`, `/api/project`, `/api/scenarios` должны вернуть 200.
2. Новый поток → START/PAUSE → ручной ввод → Compare → «До конца».
3. Лаборатория без ADMIN_TOKEN запрещена; с ключом проверьте маленькую серию
   и CSV. Лимиты совпадают с [описанием Amvera](DEPLOY_AMVERA_RU.md).
4. Откройте сайт по HTTPS в Chrome/Chromium на компьютере с Arduino.
   «Физический макет» → «Подключить Arduino» → выбрать порт → синхронизировать.
   USB не пробрасывается в Docker и не подключается к VPS.
5. Проверьте физическую кнопку, четыре сигнала, ×1 и безопасное отключение.
   Аппаратные действия — по [README макета](../hardware/README_RU.md).

Обновляйте только после проверки локальных изменений и вашего решения
публиковать их. На VPS `git pull --ff-only`, затем `docker compose -f
deploy/compose.yaml up -d --build`. Не используйте force/reset и не удаляйте
томы ради обновления. Активные сессии при рестарте исчезают; результаты в томе
сохраняются. Сделайте резервную копию тома до значимых изменений.

Остановка: `docker compose -f deploy/compose.yaml stop`. SIGTERM передаётся
Uvicorn; запущенная лаборатория получает запрос остановки и завершает текущий
отдельный опыт, последующие не запускаются. Прерванный CSV не является полным
экспериментом. Локальные старые результаты не меняются.

## Запуск без Docker

Если используете systemd и отдельный venv, команда:

```bash
python -m adaptive_traffic.serve
```

Переменные берутся из среды процесса; модуль сам не читает `.env`.
В systemd укажите WorkingDirectory, EnvironmentFile с абсолютными путями,
ExecStart `/путь/.venv/bin/python -m adaptive_traffic.serve`, User отдельного
непривилегированного пользователя, Restart=on-failure, MemoryMax=448M,
CPUQuota=25%, TasksMax=64, TimeoutStopSec=20. RESULTS_DIR должен быть доступен
на запись этому пользователю. Для systemd backend можно слушать 127.0.0.1;
reverse proxy остаётся обязательным для публичного Web Serial.

Эквивалент ручной настройки Uvicorn (HOST/PORT разворачивает shell):

```bash
python -m uvicorn adaptive_traffic.serve:app_factory --factory \
  --host "$HOST" --port "$PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" \
  --limit-concurrency 24 --backlog 32 --timeout-keep-alive 5 \
  --timeout-graceful-shutdown 15
```

TRAFFIC_ENV=production и остальные ограничения обязательны для публикации.
Локальная команда `python main.py --web` по-прежнему слушает только loopback.
