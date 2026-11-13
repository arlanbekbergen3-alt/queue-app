# Развёртывание на VPS с 1 ГБ памяти (инструкция; на сервере не выполнялась)

Порядок действий для одного сервера (Ubuntu/Debian) с Docker. **На настоящий сервер я ничего не выкладывал.** Проверено локально 13.11 на отдельном проекте compose (порты 18080 и 18443, имена `queueprodtest-*`, потом всё удалено): сборка образа, запуск `docker-compose.prod.yml` на пустой базе (postgres, web и caddy healthy), `/health` и `/ready` через Caddy, страницы, QR, поток событий SSE через прокси, `/metrics` снаружи закрыт (404), заголовки безопасности, процесс не от root, корневая файловая система только для чтения, резервная копия с ротацией и восстановление в новую базу. Не проверено: настоящий домен и выпуск сертификата Let's Encrypt (локально Caddy работал по обычному HTTP), cron, нагрузка на VPS с 1 ГБ, обновление через `git pull`.

## Что нужно

- VPS от 1 ГБ памяти, 1 vCPU (предположение: нагрузки на одну группу хватает; замеров на VPS нет). Локальный замер покоя: caddy 13 МБ, web 75 МБ, postgres 31 МБ (пределы в файле: 96, 384 и 320 МБ, в сумме больше 1 ГБ только на бумаге). Добавьте swap на 1-2 ГБ.
- Домен, A-запись которого указывает на сервер; порты 80 и 443 открыты. Остальное (5432, 8000) наружу не публикуется вовсе: у web и postgres нет `ports`, они доступны только внутри сети compose.
- Docker и Docker Compose plugin.

## 1. Код и настройки

```bash
git clone <ваш-репозиторий> queue-app && cd queue-app
cp .env.prod.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(12))"   # DB_PASSWORD, TEACHER_INVITE_CODE, METRICS_TOKEN
nano .env      # SITE_ADDRESS=ваш.домен, DB_PASSWORD, JWT_SECRET_KEY, TEACHER_INVITE_CODE, METRICS_TOKEN
chmod 600 .env
```

Приложение не запустится с пустыми секретами, значениями `change-me...`, JWT-ключом короче 32 символов или кодом приглашения короче 8 (`tests/test_ops.py`). `.env.prod.example` содержит только пустые заглушки.

## 2. Запуск

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps          # postgres, web, caddy: healthy
curl -s https://ваш.домен/ready                        # {"status":"ready"}
```

Миграции применяются при старте `web`. Процесс **один**: рассылка SSE, лимиты, счётчики перебора кода присутствия и метрики живут в памяти процесса, поэтому не запускайте несколько реплик или `--workers N` (`docs/DECISIONS.md`, п. 4).

Что сделано для безопасности в `docker-compose.prod.yml` и `Dockerfile` (проверено):

- процесс в контейнере работает под пользователем `app` (uid 10001, без оболочки), код принадлежит root и ему недоступен для записи, корневая ФС только для чтения (запись только в `/tmp` в памяти), `cap_drop: ALL`, `no-new-privileges`;
- у postgres и web нет опубликованных портов; наружу смотрит только Caddy (80 и 443);
- пределы памяти и ротация журналов (10 МБ на файл, 5 файлов на контейнер);
- postgres настроен под маленькую память (`shared_buffers=64MB`, `max_connections=40`), пул приложения 10 соединений (`DB_POOL_SIZE`);
- `AI_ENABLED=false` по умолчанию: Ollama не нужна и на 1 ГБ не запустится (включение описано в `docs/AI.md`).

`FORWARDED_ALLOW_IPS="*"` в файле допустим, потому что до `web` доходит только Caddy из внутренней сети. Не публикуйте порт 8000 наружу, иначе клиент сможет подделать свой IP в `X-Forwarded-For` и обойти лимиты по IP.

## 3. HTTPS

Caddy сам получает и продлевает сертификат Let's Encrypt для `SITE_ADDRESS` (нужны открытые 80 и 443 и корректная A-запись). Конфиг `deploy/Caddyfile`:

- `/metrics` снаружи отвечает 404, Prometheus (если появится) обращается к `web` изнутри сети и передаёт `METRICS_TOKEN`;
- `flush_interval -1` нужен, чтобы потоки SSE не буферизовались (проверено: событие приходит через прокси сразу);
- заголовки `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`; заголовок `Server` убран.

Для локальной проверки без домена задайте в `.env` `SITE_ADDRESS=:80`, `HTTP_PORT=18080`, `HTTPS_PORT=18443`.

## 4. Резервное копирование и восстановление

Скрипты в `deploy/` (проверены локально: три копии при `BACKUP_KEEP=2` оставляют две свежих; восстановление в новую базу показало те же числа и версию миграции):

```bash
# вручную
BACKUP_DIR=/var/backups/queue BACKUP_KEEP=14 bash deploy/backup.sh

# ежедневно в 03:10 (crontab -e), хранить 14 последних копий
10 3 * * * cd /home/ubuntu/queue-app && BACKUP_DIR=/var/backups/queue BACKUP_KEEP=14 bash deploy/backup.sh >> /var/log/queue-backup.log 2>&1
```

`backup.sh` делает `pg_dump -Fc`, проверяет, что архив читается (`pg_restore --list`), только потом сохраняет файл (права 600; поведение на повреждённом архиве не проверялось) и удаляет всё, что старше `BACKUP_KEEP` последних. Папка копий должна принадлежать пользователю, от которого запускается cron.

Восстановление **в новую базу** (живую базу скрипт не трогает и откажется её перезаписывать):

```bash
bash deploy/restore.sh /var/backups/queue/queue_20260101-031000.dump queue_restore
# выведет число сессий и записей и версию миграции; проверьте, что они похожи на ожидаемые
```

Чтобы заменить рабочую базу копией: остановить web (`docker compose -f docker-compose.prod.yml stop web`), переименовать базы через `psql` (`ALTER DATABASE queue_db RENAME TO queue_db_old`, затем `queue_restore` в `queue_db`), запустить web. **Копии лежат на том же сервере**: для настоящей защиты копируйте их на другой сервер или в облако (`rclone`, `scp`; предложение, не настроено). **Регулярно проверяйте восстановление**: неподтверждённая копия не считается копией. Копии содержат имена и оценки (см. `docs/PILOT.md`, раздел 5), храните их так же осторожно, как саму базу.

## 5. Чек-лист безопасности перед первым занятием

- [ ] `.env` с правами 600, секреты сгенерированы, не совпадают с примерами, в git не попали (`git status`);
- [ ] `TEACHER_INVITE_CODE` выдан только преподавателям; после регистрации всех нужных преподавателей его можно сменить;
- [ ] `METRICS_TOKEN` задан; `/metrics` снаружи отвечает 404 (`curl -s -o /dev/null -w "%{http_code}" https://ваш.домен/metrics`);
- [ ] `CORS_ORIGINS=[]`: страницы отдаются с того же домена, чужие сайты не нужны;
- [ ] открыты только 80 и 443 (`ss -tlnp`, брандмауэр хостинга); 5432 и 8000 с интернета недоступны;
- [ ] лимиты запросов оставлены по умолчанию или осознанно изменены (`RATE_LIMIT_*`, `MAX_CONCURRENT_REQUESTS`, `docs/DECISIONS.md`, п. 5-6); перебор кода присутствия ограничен отдельно (п. 37);
- [ ] `AI_ENABLED=false`, пока Ollama не развёрнута отдельно;
- [ ] срок хранения данных `DATA_RETENTION_DAYS` согласован (по умолчанию 180 дней, `docs/PILOT.md`, раздел 5);
- [ ] резервная копия сделана и **один раз восстановлена** в тестовую базу;
- [ ] `docker compose -f docker-compose.prod.yml ps` показывает `healthy`, `curl https://ваш.домен/ready` отвечает `ready`;
- [ ] ОС и Docker обновлены, вход по SSH только по ключу (вне этого репозитория).

## 6. Обновление

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build        # миграции применятся при старте web
curl -s https://ваш.домен/ready
```

Перед обновлением сделайте копию (`deploy/backup.sh`). Короткий перерыв (секунды) при перезапуске неизбежен: страницы студентов переходят на опрос и переподключают поток сами. Откат миграции: `docker compose -f docker-compose.prod.yml exec web alembic downgrade -1`; учтите, что `downgrade` удаляет данные добавленных столбцов и таблиц (`docs/KNOWN_ISSUES.md`).

## 7. Что смотреть после запуска

- `docker compose -f docker-compose.prod.yml logs -f web` (JSON, в каждой строке `request_id`).
- `/metrics` с заголовком `Authorization: Bearer <METRICS_TOKEN>` изнутри сервера: `queue_http_requests_total{status="5xx"}`, `queue_http_rejected_*`, `queue_db_pool_checked_out` (у пула 10 соединений), `queue_sse_connections`, `queue_sessions_purged_total`.
- Очистка старых данных пишет в журнал строку «Retention: removed …»; вручную: `docker compose -f docker-compose.prod.yml exec web python -m services.retention --days 180` (только посчитать) и с `--apply` (удалить).
- Рост 503 или `queue_db_pool_checked_out`, близкий к размеру пула, значит перегрузку; значения лимитов задаются переменными окружения.

## 8. ИИ-помощник на сервере (необязательно)

По умолчанию выключен. Включение и установка Ollama описаны в `docs/AI.md`; на VPS с 1 ГБ модель не запустится, подходит только отдельная машина. В `docker-compose.prod.yml` доступа к Ollama на хосте нет (в отличие от `docker-compose.yml` для разработки): его нужно добавить осознанно. Тренировка студентов раскрывает банк вопросов: включайте её только осознанно.

## Разработка и прежний файл

`docker-compose.yml` остался для локальной разработки (порты привязаны к `127.0.0.1`, имена `queue_db` и `queue_api`); боевой вариант это `docker-compose.prod.yml`.

## Чего здесь нет

Мониторинга и оповещений, автоматического копирования копий вне сервера, нескольких реплик, нагрузочных замеров на реальном VPS и в Wi-Fi колледжа, проверенного выпуска сертификата.
