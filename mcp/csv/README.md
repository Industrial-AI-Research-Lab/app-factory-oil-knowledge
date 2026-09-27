# CSV MCP

Нормализация CSV: текст или ссылка превращается в проверенные строки,
опционально — с выгрузкой результата в S3.

Здесь и далее: **MCP** — сервис, отдающий функции (tools) по протоколу
Model Context Protocol; **tool** — одна такая функция; **Inspector** —
веб-утилита для ручного вызова tools (`npx @modelcontextprotocol/inspector`).
Транспорт везде — `streamable-http` на пути `/mcp`.

Контейнер слушает `0.0.0.0:8080/mcp`, наружу — `127.0.0.1:18081`.

## Структура

Слои, сверху вниз (обратные импорты запрещены):

| Слой           | Файлы                                                 | Ответственность                                                    |
|----------------|-------------------------------------------------------|--------------------------------------------------------------------|
| Транспорт      | `src/csv_mcp/server.py`, `__main__.py`, `__init__.py` | 3 `@mcp.tool`, тонкая делегация в сервис, без доменной логики      |
| Оркестрация    | `src/csv_adapter/service.py`                          | 3 публичные операции + `_guard_cell` (защита от формул при записи) |
| Загрузка       | `src/csv_adapter/download.py`                         | Единственный модуль с сетью: HTTPS/S3 fetch, SSRF-guards           |
| Разбор         | `src/csv_adapter/parser.py`                           | Чистый строгий парсер, без I/O                                     |
| Хранение       | `src/csv_adapter/storage.py`                          | Выгрузка результата в S3 и выдача ссылки на скачивание             |
| Типы           | `src/csv_adapter/models.py`                           | Frozen Pydantic-модели, `extra="forbid"`, без I/O                  |
| Ошибки         | `src/csv_adapter/errors.py`                           | `CsvDomainError`, чужие исключения → `E_* from None`               |
| Константы      | `src/csv_adapter/config.py`                           | Только константы, без чтения env                                   |
| Ядро исходника | `src/stairs_csv/backend_file.py`                      | Изолированные `detect_csv_delimiter`, `decode_csv_bytes`           |
| Снимок         | `sources/stairs-backend/.../file.py`                  | Неизменённый исходник, в рантайме не импортируется                 |

Инструменты (ровно 3):

- `csv_normalize_inline(content, delimiter="auto")` — текст → строки + диалект (разделитель), `warnings=[]`.
- `csv_write_normalized(rows, delimiter=";")` — строки → канонический текст (UTF-8 без BOM, `\n`).
- `csv_download_normalize_upload(csv_url)` — полный цикл (максимум 8 одновременных выполнений).

## Настройка

Env (только имена; пусто = fail-closed отказ):

| Переменная               | Назначение                                          |
|--------------------------|-----------------------------------------------------|
| `CSV_ALLOWED_HOSTS`      | Allowlist HTTPS-хостов                              |
| `CSV_ALLOWED_S3_BUCKETS` | Allowlist S3-бакетов чтения и записи                |
| `S3_RESULT_BUCKET`       | Целевой бакет результата                            |
| `S3_RESULT_PREFIX`       | Префикс ключа (дефолт `normalized` при unset/blank) |
| `PORT`                   | Порт внутри контейнера (дефолт 8080)                |

Креды AWS (`AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY/ENDPOINT_URL/DEFAULT_REGION`) — только через host `.env` в compose, в
образ не запекаются.

Кодовые лимиты (`config.py`): текст 200000 символов, 2000 строк, 16 колонок, ячейка 16000, структуры/рёбра по 64,
вложенность 3; скачивание 10 МБ / 15 с / 3 редиректа; presigned TTL 3600 с. Канон:
`activity_id, activity_name, volume, measurement` обязательны; `structure, edges, granular_unit` опциональны;
разделители `;`/`,`. Кодировки `utf-8-sig → cp1251`. Ячейки с `= + - @ | %` при записи квотируются (`W_FORMULA_QUOTED`).

Ошибки — только `E_*` без URL, тел и сырых ячеек; неожиданное — `E_INTERNAL`.

## Обновление

**Изменился исходник (`stairs-backend/.../file.py`):** заменить снимок в `sources/`, обновить хеш в
`sources/SOURCES.md`;
`src/stairs_csv/backend_file.py` поправить вручную минимально (только fallback кодировок и детект разделителя).
Persistence/Pandas/XER/XLSX не тащить.

**Нужны изменения в самом MCP:** логика — только в `csv_adapter`, транспорт держать тонким. Новые лимиты/коды — в
`config.py`. Контрактные тесты (`tests/test_*`) держат направление зависимостей и число инструментов.

**Зависимости:** `requirements.txt` — `mcp[cli]==1.16.0`, `pydantic==2.11.9`, `boto3>=1.34`.

## Сценарии использования

Выполнение — через Inspector: подключение к `http://127.0.0.1:18081/mcp`
(транспорт Streamable HTTP), выбор tool, ввод аргументов, запуск.

- **Нормализация текста.** Tool `csv_normalize_inline`. Вход — CSV-текст
  (разделитель `;`/`,` определяется автоматически). Ответ — строки
  с типами и найденный разделитель. Пустой текст — ошибка `E_EMPTY_CONTENT`.
- **Полный цикл через файл.** Tool `csv_download_normalize_upload`. Требуются
  MinIO с бакетом и `.env` (см. `mcp/TESTING.md` §0–1). Пример:
  `{"csv_url": "s3://test/demo_in.csv"}`. Ответ — число строк и
  https-ссылка на нормализованный файл. Ссылка вне allowlist —
  `E_URL_FORBIDDEN`, неподдерживаемая кодировка — `E_ENCODING_UNSUPPORTED`.
- **Запись обратно.** Tool `csv_write_normalized`. Вход — строки (например, из
  первого сценария), ответ — готовый текст. Ячейки с `=`, `+`, `@`
  в начале автоматически квотируются, что исключает выполнение чужих формул
  при открытии таблицы в Excel.

## Проверка

- L1: `compose up` + `scripts/smoke.py` (ждёт ровно 3 инструмента).
- L2: `pytest mcp/csv/tests -q -p no:cacheprovider` (моки, без сети).
- L3: Inspector, `http://127.0.0.1:18081/mcp` (примеры — выше, S1–S3).

Проверка живости в compose контролирует только доступность порта, но не работоспособность инструментов. Порт должен
оставаться на localhost: авторизации у сервиса нет, публикация в интернет допустима только через прокси с TLS.
