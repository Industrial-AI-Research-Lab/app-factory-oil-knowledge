# Semantic Mapping MCP

Подбирает эталонные названия для работ и иерархий, написанных в свободной
форме. На входе — название «как написали» (с опечатками, синонимами,
лишними словами), на выходе — список эталонов из справочника, отсортированный
по близости, с числовой дистанцией для каждого (чем меньше, тем ближе).

Как ищется: текст превращается в вектор (эмбеддинг через TEI-сервис),
затем в базе Postgres с расширением pgvector находятся ближайшие векторы
эталонов. Сервис только читает справочники, ничего не пишет.

Контейнер слушает `0.0.0.0:8080/mcp`, наружу торчит `127.0.0.1:18080`.

## Структура

| Слой           | Файлы                                               | Ответственность                                                                                                                                                  |
|----------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Транспорт      | `src/semantic_mapping_mcp/server.py`, `__main__.py` | 2 `@mcp.tool`, валидация границы, слоты параллелизма, редакция ошибок                                                                                            |
| Конфиг         | `src/semantic_mapping_adapter/config.py`            | `DBConfig` + `RuntimeSettings` (pydantic-settings, frozen); env читается только в `load_runtime_settings`                                                        |
| Инфра          | `src/semantic_mapping_adapter/infrastructure.py`    | `SecurePostgresConnectionProvider` (пароль только у `connect`, read-only scope) + `ValidatingPgVectorStore` (проверки поверх источникового стора). SQL не строит |
| Поиск работ    | `src/semantic_mapping_adapter/works.py`             | Двухстадийный источниковый `HierarchicalMapper` (категории → имена)                                                                                              |
| Поиск иерархий | `src/semantic_mapping_adapter/hierarchies.py`       | Одностадийный источниковый `SimpleMapper` с фильтром уровня                                                                                                      |
| Общее          | `src/semantic_mapping_adapter/_common.py`           | Приватные `unwrap_tei_token`, `to_distances` (приватный модуль, не публичный API)                                                                                |
| Ядро           | `src/stairs_semantic_mapping/`                      | 9 файлов байт-в-байт со снимком + 2 шима; SQL и ранжирование только здесь                                                                                        |
| Снимок         | `sources/stairs-semantic-mapping/`                  | Неизменённый снимок исходника, в рантайме не импортируется                                                                                                       |

Инструменты (ровно 2, read-only):

- `semantic_search_works(works, top_k=5)` — работы → эталоны в порядке входа, с дистанциями.
- `semantic_search_hierarchies(hierarchies, level, top_k=5)` — иерархии → эталоны с фильтром `level`.

## Настройка

Env (`RuntimeSettings`, `extra="ignore"`, frozen; секреты — `SecretStr`, в логах/ошибках не светят):

| Переменная                                                 | Назначение                                                                                                    |
|------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `DB_CONFIG`                                                | **Обязательна.** JSON `{host, port, dbname, user, password[, connect_timeout=5, statement_timeout_ms=15000]}` |
| `EMBEDDING_HOST_CATEGORIES` / `_TASKS` / `_HIERARCHIES`    | TEI-эндпоинты (дефолт `http://embedder/embed`; только `http(s)`, без credentials/fragment)                    |
| `EMBEDDING_ACCESS_TOKEN`                                   | Опциональный bearer-токен TEI                                                                                 |
| `TOP_K_NAMES=1` (1–10) / `TOP_K_CATEGORIES=5` (1–50)       | Дефолтные top-k маппера                                                                                       |
| `CATEGORY_ID_FIELD` / `CATEGORY_NAME_FIELD`                | Имена мета-колонок категорий                                                                                  |
| `BATCH_SIZE_CATEGORIES=32` / `BATCH_SIZE_TASKS=32` (1–128) | Батчи TEI (второй — также для иерархий)                                                                       |
| `PORT`                                                     | Порт внутри контейнера (дефолт 8080)                                                                          |

Границы транспорта (`server.py`): батч 1–32, `top_k` 1–10, текст 4096 на поле / 32768 суммарно, 8 concurrent (иначе
`capacity exceeded`). DB: statement timeout через `options`, read-only транзакции, один курсор на запрос.

## Обновление

**Изменился исходник:** заменить снимок в `sources/`, перенести локальные патчи (гарды `embeddings.py`), обновить хеш в
`SOURCES.md`, перекопировать 9 файлов 1-в-1 в `src/stairs_semantic_mapping/` (ядро руками не править), прогнать
`pytest` (есть SHA-тест идентичности). Идеал — принять патчи апстримом и вернуться к чистому снимку.

**Нужны изменения в самом MCP:** править `server.py` / `semantic_mapping_adapter/*.py`; DDL таблиц (`granular_category`,
`granular_name`, `semantic_hierarchical_works`) — вне этого репозитория. Контрактные тесты сверяют 2 tool, ленивый env,
делегацию и редакцию ошибок.

**Зависимости:** `requirements.txt` — `mcp[cli]==1.16.0`, `pydantic==2.11.9`; `numpy`, `psycopg2-binary`,
`pydantic-settings`, `requests`, `uvicorn` (минимумы).

## Сценарии использования

Выполнение — через Inspector на `http://127.0.0.1:18080/mcp`. Требуются
сидированные база Postgres и TEI (см. `mcp/TESTING.md` §0, §3); без них —
ошибка подключения, что является ожидаемым поведением.

- **Поиск работы.** Tool `semantic_search_works`, аргументы:
  `{"works": [{"work_name": "Foundation work", "work_measurement": "m3"}], "top_k": 2}`.
  Ответ — 2 ближайших эталона, первый `Foundation pouring` с наименьшей
  дистанцией. Заведомо чужое название — не ошибка: возвращаются далёкие соседи
  с большими дистанциями.
- **Поиск иерархии.** Tool `semantic_search_hierarchies`, аргументы:
  `{"hierarchies": ["Concrete works"], "level": 1, "top_k": 2}`.
  В ответе — топ-1 с кодом `H1`. Несуществующий уровень (например, 99) —
  пустой результат без ошибки.
- **Ошибки до сети.** Пустой список, более 32 элементов, `top_k` вне 1–10,
  пустые строки — ошибка валидации сразу, без обращений к базе.
  Перегрузка (более 8 параллельных запросов) — `capacity exceeded`.

## Проверка

- L1: `compose up` + `scripts/smoke.py` (только discovery, 2 инструмента).
- L2: `pytest mcp/semantic-mapping/tests -q -p no:cacheprovider` (моки).
- L3: Inspector, `http://127.0.0.1:18080/mcp` (нужны сидированные PG+TEI — см. `mcp/TESTING.md`; без них — ожидаемый
  сбой подключения).
