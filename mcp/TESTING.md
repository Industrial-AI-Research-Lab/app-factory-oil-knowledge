# MCP: проверка с нуля

Что умеет каждый MCP — в его README (`mcp/<имя>/README.md`, раздел «Сценарии»).
Здесь — полный путь: инфра, сиды, запуск, проверка через Inspector.

Соответствие портов: `semantic → 18080`, `csv → 18081`, `planning → 18082`
(внутри контейнеров везде `8080`).

> Все службы ниже предназначены только для локальной проверки и запускаются
> без авторизации. Не публикуйте MCP, PostgreSQL, MinIO API и TEI на
> `0.0.0.0`/`::` и не используйте эту конфигурацию на общем сервере или
> машине, доступной из сети. Команды `docker run -p X:Y` из инструкции
> привязывают порт ко всем интерфейсам — это осознанный риск одноразового
> стенда на доверенном хосте. Доступ к Docker daemon эквивалентен
> административным правам на машине.
>
> Пароли ниже (`minioadmin/minioadmin123`, `plan/plan123`, `sem/sem123`)
> намеренно тестовые. Не использовать их вне изолированного стенда.
> Файлы `.env` не должны попадать в git (проверить `git status` и `.gitignore`).

Всё ниже — тестовые одноразовые данные; выдуманные цифры сидов проверяют
только сквозную передачу данных и контракты, но не качество оценок
(оно проверяется на демо-стенде).

## 0. Инфра (один раз)

```bash
# S3-совместимое хранилище с TLS (presigned-ссылки http:// отклоняются проверкой _require_https_url → E_UPLOAD_FAILED).
# Сначала определить адрес docker0 и сертификат на него + localhost:
DOCKER_HOST_IP=$(ip addr show docker0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
mkdir -p ~/minio-certs && cd ~/minio-certs && openssl req -x509 -newkey rsa:2048 \
  -keyout private.key -out public.crt -days 30 -nodes -subj "/CN=localhost" \
  -addext "subjectAltName=IP:${DOCKER_HOST_IP},DNS:localhost" && cp public.crt ca.crt
# Далее — сам MinIO (сертификат выше уже выписан на ${DOCKER_HOST_IP}):
sudo -g docker docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -v minio-data:/data -v /home/user/minio-certs:/root/.minio/certs:ro \
  minio/minio server /data --console-address ":9001"
# Консоль теперь https://localhost:9001 (браузер предупредит о самоподписи).
# БД моделей planning
sudo -g docker docker run -d --name plan-db -p 5432:5432 \
  -e POSTGRES_USER=plan -e POSTGRES_PASSWORD=plan123 -e POSTGRES_DB=models postgres:16
# БД semantic (pgvector, другой порт чтобы не путать)
sudo -g docker docker run -d --name sem-db -p 5433:5432 \
  -e POSTGRES_USER=sem -e POSTGRES_PASSWORD=sem123 -e POSTGRES_DB=semantic \
  pgvector/pgvector:pg16
# TEI (CPU; MiniLM ~90МБ — для слабых хостов; e5-small только при 8+ ГБ RAM,
# т.к. на 6 ГБ контейнер убивает OOM killer (exit 137) при загрузке весов)
sudo -g docker docker run -d --name tei -p 8081:80 \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.6 \
  --model-id sentence-transformers/all-MiniLM-L6-v2 --max-client-batch-size 32
# без GPU флаг --gpus all убери (он вообще не нужен для cpu-образа)
```

Переменная `DOCKER_HOST_IP` определена в начале §0 и используется во всех
адресах ниже. Проверка опубликованных портов после запуска:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

В выводе не должно быть неожиданных `0.0.0.0:...`/`[::]:...` за пределами
этого сценария.

## 1. CSV: бакет, файлы, запуск

В консоли `https://localhost:9001` (принять самоподпись) создаётся бакет `test`, загружается `demo_in.csv`:
```csv
activity_id;activity_name;volume;measurement
A1;Foundation;10;m3
A2;Walls;20;m2
```
`mcp/csv/.env` (начать с `.env.example`; проверить `git status` — файл
не должен попасть в коммит):
```env
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin123
AWS_ENDPOINT_URL=https://${DOCKER_HOST_IP}:9000
AWS_DEFAULT_REGION=us-east-1
CSV_ALLOWED_S3_BUCKETS=test
S3_RESULT_BUCKET=test
S3_RESULT_PREFIX=demo
```
Дополнительно `mcp/csv/compose.override.yaml` (в git-игноре, не коммитить) — доверенный
CA и https-endpoint, без которых `boto3` отклоняет самоподпись MinIO:
```yaml
services:
  csv:
    volumes:
      - /home/user/minio-certs/ca.crt:/certs/ca.crt:ro
    environment:
      AWS_CA_BUNDLE: /certs/ca.crt
      AWS_ENDPOINT_URL: https://${DOCKER_HOST_IP}:9000
```
```bash
cd mcp/csv && sudo -g docker docker compose up -d --force-recreate
```

## 2. Planning: сид, запуск

```bash
~/.venvs/mcpsmoke/bin/pip install "psycopg2-binary"
~/.venvs/mcpsmoke/bin/python mcp/planning/scripts/seed_demo_db.py
```
`mcp/planning/.env` (начать с `.env.example`, не коммитить):
```env
RM_ADAPTER_CONN_STR=postgresql+psycopg2://plan:plan123@${DOCKER_HOST_IP}:5432/models
PLANNING_CSV_ALLOWED_S3_BUCKETS=test
```
```bash
cd mcp/planning && sudo -g docker docker compose up -d
```
Файл `demo_project.csv` из `mcp/planning/scripts/` загрузить в тот же
MinIO-бакет `test` под именем `plan.csv` (сид залит ровно под его 5 работ: `Site preparation`,
`Foundation pouring`, `Walls erection`, `Roofing`, `Finishing works`).
Особенность сида: модель ищется по granular-имени (для `A5` ключ —
`('Finishing', 'civil', 'm2')`); для новой работы с granular в сид добавляется строка
под её `(name, category, measurement)`.

## 3. Semantic: сид, запуск

```bash
~/.venvs/mcpsmoke/bin/pip install "psycopg2-binary" "requests"
DEMO_DSN="postgresql://sem:sem123@127.0.0.1:5433/semantic" \
DEMO_TEI="http://127.0.0.1:8081/embed" \
~/.venvs/mcpsmoke/bin/python mcp/semantic-mapping/scripts/seed_demo_index.py
```
`mcp/semantic-mapping/.env` (начать с `.env.example`, не коммитить):
```env
DB_CONFIG={"host": "${DOCKER_HOST_IP}", "port": 5433, "dbname": "semantic", "user": "sem", "password": "sem123"}
EMBEDDING_HOST_CATEGORIES=http://${DOCKER_HOST_IP}:8081/embed
EMBEDDING_HOST_TASKS=http://${DOCKER_HOST_IP}:8081/embed
EMBEDDING_HOST_HIERARCHIES=http://${DOCKER_HOST_IP}:8081/embed
```
```bash
cd mcp/semantic-mapping && sudo -g docker docker compose up -d
```

## 4. Проверка через MCP Inspector

```bash
node --version  # для ветки v2 нужен Node >= 22.19.0
npx @modelcontextprotocol/inspector@2.6.0
```

Открывается UI (обычно `http://127.0.0.1:6274`). Для каждого MCP: Transport Type —
`Streamable HTTP`, URL — соответственно `http://127.0.0.1:18081/mcp`,
`...:18082/mcp`, `...:18080/mcp`, Connect → List Tools → выбор tool → ввод
аргументов → Run.

Быстрая проверка транспорта без инфры: `python mcp/<имя>/scripts/smoke.py`
(проверяет discovery + точный набор tools).

Готовые вызовы с ожиданиями — в README пакетов, раздел «Сценарии использования»:
`mcp/csv/README.md`, `mcp/planning/README.md`, `mcp/semantic-mapping/README.md`.

## 5. Уборка

```bash
sudo -g docker docker stop minio plan-db sem-db tei
cd mcp/csv && sudo -g docker docker compose down
cd ../planning && sudo -g docker docker compose down
cd ../semantic-mapping && sudo -g docker docker compose down
```

Данные сохраняются в volumes; перезапуск — `docker start ...`.
Полное удаление тестовых данных вместе с томами:

```bash
sudo -g docker docker rm -v minio plan-db sem-db tei
```
