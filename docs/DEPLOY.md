# Развёртывание на VPS (инструкция; на сервере не выполнялась)

Здесь описан порядок действий для одного сервера (Ubuntu/Debian) с Docker. **Деплой на сервер я не делал.** Проверено локально 11.11: `docker compose build --no-cache`, запуск на пустой базе (postgres и web healthy, `/ready` отвечает `{"status":"ready"}`, процесс не от root) и раздел 4 (резервная копия и восстановление). Всё остальное (Caddy, HTTPS, `FORWARDED_ALLOW_IPS`, cron, обновление через `git pull`) предположение, его нужно пройти на настоящем сервере.

## Что нужно

- VPS от 1 ГБ памяти, 1 vCPU (предположение: нагрузки на одну группу хватает; замеров на VPS нет).
- Домен, A-запись которого указывает на сервер.
- Docker и Docker Compose plugin.
- Открыты порты 80 и 443, остальные (в том числе 8000 и 5434) наружу не открывать: в `docker-compose.yml` они привязаны к `127.0.0.1`.

## 1. Код и настройки

```bash
git clone <ваш-репозиторий> queue-app && cd queue-app    # git-репозиторий создаёте вы сами
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # для JWT_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(12))"   # для TEACHER_INVITE_CODE и DB_PASSWORD
nano .env        # заполните DB_PASSWORD, JWT_SECRET_KEY, TEACHER_INVITE_CODE
chmod 600 .env
```

Приложение не запустится с пустыми секретами, значениями `change-me...`, JWT-ключом короче 32 символов или кодом приглашения короче 8 (проверено локально: `tests/test_ops.py`).

Важные переменные (полный список с умолчаниями в `.env.example`):

| Переменная | Что делает |
|---|---|
| `TEACHER_INVITE_CODE` | код, без которого преподаватель не зарегистрируется; раздавайте только преподавателям |
| `METRICS_TOKEN` | если задан, `/metrics` требует `Authorization: Bearer <токен>` |
| `FORWARDED_ALLOW_IPS` | IP прокси, которому доверяем заголовок `X-Forwarded-For` (нужен для лимитов по IP) |
| `MAX_QUEUE_SIZE`, `MAX_ENTRIES_PER_SESSION` | размер очереди и записей за сессию |
| `RATE_LIMIT_*`, `MAX_CONCURRENT_REQUESTS` | защита от перегрузки (см. `docs/DECISIONS.md`, п. 5-6) |
| `SESSION_AUTO_CLOSE_HOURS` | через сколько часов без активности закрывать забытую сессию (0 отключает) |

## 2. Запуск

```bash
docker compose up -d --build
docker compose ps                         # postgres и web должны быть healthy
curl -s http://127.0.0.1:8000/ready       # {"status":"ready"}
```

Миграции применяются при старте контейнера `web` (`alembic upgrade head`). Процесс **один**: рассылка SSE, лимиты и метрики хранятся в памяти процесса. Не запускайте несколько реплик и `--workers N` без доработки (Redis или `LISTEN/NOTIFY`, см. `docs/DECISIONS.md`, п. 4).

## 3. HTTPS через reverse proxy (пример для Caddy)

Caddy сам получает и продлевает сертификат Let's Encrypt.

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
queue.example.com {
    encode gzip
    @metrics path /metrics
    respond @metrics 404
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
EOF
sudo systemctl reload caddy
```

- `flush_interval -1` нужен, чтобы потоки SSE (`/events`) не буферизовались прокси.
- Блок `@metrics` скрывает `/metrics` от интернета; Prometheus (если появится) ходит на `127.0.0.1:8000/metrics` напрямую.
- Для nginx: `proxy_buffering off;`, `proxy_read_timeout 1h;`, заголовки `X-Forwarded-For`.

**IP клиента (предположение, не проверено):** приложение видит адрес, с которого пришёл прокси. Для Docker с опубликованным портом это обычно адрес шлюза сети compose. Узнайте его и пропишите в `.env`, затем перезапустите:

```bash
docker network inspect queue-app_default -f '{{(index .IPAM.Config 0).Gateway}}'
# FORWARDED_ALLOW_IPS=<этот адрес>
docker compose up -d
```

Если не задать, лимиты по IP будут считать всех студентов одним клиентом (адресом прокси). Лимиты щедрые (6000 запросов в минуту на IP), но проверьте на пилоте.

## 4. Резервное копирование БД

Проверено локально на тестовом стеке с тем же `docker-compose.yml` (имена контейнеров были другие): `pg_dump -Fc`, затем `pg_restore --no-owner` в новую базу `queue_restore`; после восстановления число записей, сессий и версия миграции совпали с исходными. Не проверено: cron, удаление старых копий, восстановление поверх существующей базы.

```bash
sudo mkdir -p /var/backups/queue && sudo chown "$USER" /var/backups/queue && chmod 700 /var/backups/queue   # владелец тот, от кого запускается cron
# ежедневная копия в 03:10, хранить 14 дней (crontab -e от пользователя с доступом к docker)
10 3 * * * docker exec queue_db pg_dump -U postgres -Fc queue_db > /var/backups/queue/queue_$(date +\%F).dump && find /var/backups/queue -name 'queue_*.dump' -mtime +14 -delete
```

Восстановление в новую базу и проверка:

```bash
docker exec queue_db psql -U postgres -c "CREATE DATABASE queue_restore"
docker exec -i queue_db pg_restore -U postgres -d queue_restore --no-owner < /var/backups/queue/queue_2026-01-01.dump
docker exec queue_db psql -U postgres -d queue_restore -c "SELECT count(*) FROM entries"
```

Дамп формата `-Fc` бинарный: на Windows в PowerShell перенаправление `>` его портит, делайте команду в Git Bash или `docker exec ... pg_dump -f` внутри контейнера и `docker cp` (на сервере Linux это не касается). Копии лежат на том же сервере: для настоящей защиты копируйте их на другой сервер или в облако (`rclone`, `scp`). **Проверку восстановления делайте регулярно**, копия, которую не пробовали восстановить, не считается.

## 4.1. ИИ-помощник на сервере (необязательно)

По умолчанию выключен. Включение (`AI_ENABLED=true`) и установка Ollama описаны в `docs/AI.md`. Для доступа контейнера к Ollama на хосте в `docker-compose.yml` добавлен `extra_hosts: host.docker.internal:host-gateway`; Ollama на хосте должна принимать подключения не только с `127.0.0.1` (**не проверено на Linux-сервере**). Модель на процессоре медленная, а на VPS с 1 ГБ памяти не запустится. Тренировка студентов раскрывает банк вопросов: включайте её только осознанно.

## 5. Обновление

```bash
git pull
docker compose up -d --build        # миграции применятся при старте web
curl -s http://127.0.0.1:8000/ready
```

Короткий перерыв (секунды) при перезапуске неизбежен; студенты в этот момент автоматически переходят на опрос и переподключают поток.

## 6. Что смотреть после запуска

- `docker compose logs -f web` (логи в JSON, в каждой строке `request_id`).
- `curl -s http://127.0.0.1:8000/metrics`: `queue_http_requests_total{status="5xx"}`, `queue_http_rejected_*` (отказы по лимитам), `queue_db_pool_checked_out` (у пула 30 соединений), `queue_sse_connections`.
- Рост числа 503 или `queue_db_pool_checked_out`, близкий к 30, значит перегрузку; значения лимитов настраиваются переменными окружения.

## Чего здесь нет

Мониторинга и оповещений, автоматического резервного копирования вне сервера, нескольких реплик, нагрузочных замеров на реальном VPS и в Wi-Fi колледжа.
