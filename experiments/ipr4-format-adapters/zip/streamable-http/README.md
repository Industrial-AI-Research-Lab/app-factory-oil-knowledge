# MCP — SEG-Y / WITSML / SCADA (пункт 3.1.4 ТЗ)

Тонкий read-only адаптер по эталону `docs/mcp-server-templates/zip/streamable-http`.  
Готовый чужой MCP не используется; внутри: `segyio==1.9.14`, ElementTree, `httpx==0.28.1`, `mcp[cli]==1.16.0`.

Данные **синтетические** и лежат в отдельном сервисе `synthetic-data`, не в этом образе. Производственных подключений нет. Запись в источники в tools нет.

## Tools

| Tool | Источник | По умолчанию |
|---|---|---|
| `read_segy_headers` | бинарный SEG-Y, только заголовки | `segy/ok_rev1.sgy` |
| `read_witsml_well` | WITSML 1.4.1.1 объект `well` | `witsml/ok_well_1411.xml` |
| `read_scada_snapshot` | `GET` адреса из `SCADA_BASE_URL`. Другой хост или порт → `error` / `url_not_allowed` | `{SCADA_BASE_URL}/v1/telemetry` |

Публичный id в AppFactory: tool карточки LiteLLM
(например `ipr4_format_adapters-read_segy_headers`).

Ответ всегда JSON:

```json
{
  "status": "ok|empty|incomplete|unsupported_version|corrupt_input|timeout|error",
  "reason": "…",
  "format": "segy|witsml|scada",
  "format_version": "…",
  "source": "…",
  "data": {},
  "missing_fields": []
}
```

`value: null` в SCADA — отсутствие измерения, не ноль. `value: 0` — законный ноль.

`status` описывает верхнюю запись: первую скважину или поля снимка телеметрии. Пропуски в остальных скважинах и в точках перечислены в `missing_fields` как `wells[i].field` и `points[i].field` и сами по себе `status` не меняют.

## Локальные тесты (без Docker)

Нужны Python 3.11+ и пакеты из `requirements.txt`.

```powershell
cd AppFactory/experiments/ipr4-format-adapters/zip/streamable-http
pip install -r requirements.txt
$env:PYTHONPATH = "$pwd/src"
python -m format_adapters_mcp.generate_fixtures
python tests/test_adapters.py
```

## Локальный smoke образа

```powershell
cd AppFactory/experiments/ipr4-format-adapters/zip/streamable-http
pip install -r requirements.txt
docker compose up --build -d --wait
python scripts/smoke.py
docker compose down
```

На стенде MCP поднимают отдельно, не через Import from ZIP:

`docker build -t ipr4-format-adapters-mcp .` и
`docker run -d --name ipr4-format-adapters-mcp -p 18083:8080 ipr4-format-adapters-mcp`.

Порт `18083`, потому что `18080`–`18082` уже заняты. Данные по-прежнему `http://10.0.15.21:18090`.

И Lab, и внешний AppFactory регистрируют один LiteLLM
(`https://admin.example.com/ipr4_format_adapters/mcp`), не прямой `:18083`.
Карточка `ipr4-format-adapters-mcp` — remote HTTP, в Built images её образа нет.

В образе по умолчанию три адреса смотрят на `http://10.0.15.21:18090`. Compose и `docker run -e` могут перекрыть. Без них инструмент отвечает `source_not_configured`.

Локальный compose: MCP на `http://127.0.0.1:18080/mcp`, данные на `http://127.0.0.1:18090`.

## Синтетические fixtures

Генератор: `src/format_adapters_mcp/generate_fixtures.py`.

| Файл | Ожидаемый `status` |
|---|---|
| `segy/ok_rev1.sgy`, `segy/ok_rev0.sgy` | `ok` |
| `segy/unsupported_rev2.sgy` | `unsupported_version` |
| `segy/corrupt.sgy` | `corrupt_input` |
| `segy/empty.sgy` | `empty` |
| `witsml/ok_well_1411.xml` | `ok` (uid `SYN-WELL-001`) |
| `witsml/incomplete_*.xml` | `incomplete` |
| `witsml/empty_no_well.xml` | `empty` |
| `witsml/unsupported_1311.xml`, `unsupported_20.xml` | `unsupported_version` |
| `witsml/corrupt.xml` | `corrupt_input` |
| `GET /v1/telemetry` | `ok`, value `12.5` |
| `GET /v1/telemetry/null` | `ok`, value `null` |
| `GET /v1/telemetry/zero` | `ok`, value `0` |
| `GET /v1/telemetry/incomplete` | `incomplete` |
| `GET /v1/telemetry/nested-gap` | `ok`, пропуск `points[1].unit` |
| `GET /v2/telemetry` | `unsupported_version` |
| `GET /v1/hang` | `timeout` |
| `GET /v1/error` | `error` (`http_503`) |
| POST/PUT/PATCH/DELETE на mock | HTTP 405 |

SEG-Y: textual 3200 + binary 400 байт; 240 трасс по 1000 отсчётов, чтобы `segyio.open` открыл файл. Tool трассы не читает.

WITSML: namespace `http://www.witsml.org/schemas/1series`, поля `uid`, `name`, `field`, `timeZone`, `wellheadElevation`+`uom`.

SCADA JSON: `tag`, `value`, `unit`, `measured_at`, `source` — контракт **нашего** mock API.
