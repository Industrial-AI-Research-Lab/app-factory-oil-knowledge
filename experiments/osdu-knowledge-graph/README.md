# AppFactory-244 — OSDU knowledge graph demo

Демо загружает воспроизводимый OSDU WKS-срез Volve в FalkorDB и дает пользователю
AppFactory доступ к графу только на чтение через официальный FalkorDB MCP.

Это OSDU-совместимый property graph, а не полноценная OSDU Data Platform и не
RDF/OWL-онтология.

## Отличие от расширенного эксперимента

Это базовый эксперимент: он проверяет поиск связей «скважина → ствол → лог» и
ответы агентов через read-only MCP на небольшом фиксированном наборе данных.
Граф `osdu-volve` содержит 66 узлов и 55 связей; временных рядов добычи и
географического обогащения SODIR в этом эксперименте нет.

[Расширенный эксперимент](../osdu-kg-extended/README.md) копирует эти сущности в
отдельный `osdu-volve-extended`, добавляет добычу, географию и происхождение данных.
Он предназначен для аналитических Cypher-запросов и не включает собственный
workflow агентов AppFactory. [Обзор всех экспериментов](../README.md).

## Архитектура

```text
OSDU Open Test Data / Volve
  -> graph_loader fetch / validate / normalize
  -> build/graph.json
  -> idempotent FalkorDB MERGE (osdu-volve)
  -> strict read-only FalkorDB MCP :8081
  -> workflow osdu_graph_demo в AppFactory
```

Стек FalkorDB поднимается базовым compose из `docs/graph-db-poc/compose/`.  
Overlay `compose/docker-compose.osdu.yml` добавляет отдельный MCP на `:8081` с
`FALKORDB_DEFAULT_READONLY=true` и `FALKORDB_STRICT_READONLY=true`.  
На `:8080` остается обычный MCP того же стека (read/write); OSDU-агент к нему
не подключается.

## Пайплайн

Пользователь задает вопрос в чате AppFactory (workflow `osdu_graph_demo`).
Дальше вопрос идет по контуру аналитик → критик → шлюз; к пользователю
попадает только принятый текстовый ответ.

```text
пользователь
    │  вопрос
    ▼
аналитик  ── читает граф (query_graph_readonly, get_node_schema) ── ► черновик
    ▲                                                                │
    │                         доработка (до 5 раз)                   ▼
    └──────────────────── шлюз ◄── критик (без инструментов)
                              │
                              │  accept
                              ▼
                         пользователь
```

Аналитик пишет черновик `osdu_analysis_report` по рядам MCP. Критик граф
не читает: смотрит, ответил ли черновик на вопрос. Если это dump, Cypher,
уточнение к пользователю или не ответ — весь вывод начинается с `REVISE` и
кратко указывает, что переделать. Если черновик уже ответ — критик копирует
факты в `osdu_user_answer` без MATCH / JSON инструментов.

Шлюз смотрит `osdu_user_answer`: `REVISE` и сырой инструментный текст
возвращают ход аналитику (тот же вопрос, до пяти кругов). Accept завершает
чат. Если шлюз так и не принимает текст, проект падает. Критик проверяет форму, не эталон Q1–Q10: ложные факты при accept все равно уходят пользователю.

## Данные и модель

Источник и версия зафиксированы в [DATA_VERSION.json](DATA_VERSION.json):

- [OSDU Open Test Data / Volve — закреплённый срез](https://community.opengroup.org/osdu/data/open-test-data/-/tree/752429501511f05176faab2e916a90c1f8e88a50/rc--3.0.0/4-instances/Volve);
- commit `752429501511f05176faab2e916a90c1f8e88a50`;
- 11 Well, 27 Wellbore, 28 WellLog.

Граф:

```text
(:Wellbore)-[:BELONGS_TO_WELL]->(:Well)
(:WellLog)-[:BELONGS_TO_WELLBORE]->(:Wellbore)
```

`osduId` нормализуется (URL-decode, один завершающий `:`). WellLog без полного
id получает детерминированный `urn:osdu-demo:WellLog:<sha256>`. Узлы и связи
пишутся через `MERGE` по `osduId`.

## Восстановление данных

Каталоги `data/` и `build/` исключены из Git. Если они потеряны, исходные JSON
повторно скачиваются из публичного репозитория OSDU по ссылке выше. Загрузчик
использует GitLab API `https://community.opengroup.org/api/v4` и commit из
[DATA_VERSION.json](DATA_VERSION.json), а не текущую ветку репозитория.

Внутри `rc--3.0.0/4-instances/Volve` нужны три каталога:

- `master-data/Well` — 11 JSON;
- `master-data/Wellbore` — 27 JSON;
- `work-products/welllogs_1_1_0` — 28 JSON с метаданными WellLog.

Для восстановления файлов достаточно `fetch` и `build` из раздела ниже:
`fetch` создаёт `data/raw/` с исходной структурой и `SNAPSHOT.json` (список файлов,
размеры и SHA-256), `build` пересобирает `build/graph.json`. Доступ к БД для этих
двух этапов не нужен. Полный архив Volve и двоичные LAS-файлы скачивать не требуется.

Если потерян и граф в FalkorDB, после восстановления файлов выполните `load` и
`verify`; команда `all` выполняет весь цикл, начиная со скачивания. Подготовка
окружения и БД описана в [INSTALL-LOCAL.md](INSTALL-LOCAL.md).

## Loader

Команды выполняются из `experiments/osdu-knowledge-graph` в окружении Python 3.11+
с зависимостями из [requirements.txt](requirements.txt):

```powershell
python -m graph_loader fetch
python -m graph_loader build
python -m graph_loader load
python -m graph_loader verify
python -m graph_loader all
```

Пути по умолчанию привязаны к этой папке.  
`fetch` атомарно публикует snapshot с SHA-256 inventory.  
`load` пишет напрямую в FalkorDB (не через MCP) и сохраняет `build/load-report.json`.

## Read-only

1. MCP `:8081` — strict read-only.
2. Allowlist OSDU-агента — только `query_graph_readonly` и `get_node_schema`.

Vendor MCP при discovery по-прежнему отдаёт полный список tools; write-tools
агенту не выдаются, а `CREATE` через `query_graph` на `:8081` отклоняется.
Smoke проверяет отказ и постусловие «0 записанных маркеров».

## Контрольные вопросы и ожидаемые ответы

Эталоны живут в коде (`graph_loader/controls.py`, `demo/run_osdu_demo.py`).
Агенту они **не** подсказываются: модель каждый раз читает граф через MCP.
Ниже — тексты для UI / ручной проверки.

**Q1.** Какие wellbores у скважины 15/9-19?  
Ожидание: `15/9-19 A`, `15/9-19 B`, `15/9-19 S`, `15/9-19 SR`, `15/9-19 SR2`.

**Q2.** Какие well logs у wellbore 15/9-19 SR (NPD-2105)?  
Ожидание: `15_9-19_SR_CPI.las`, `STAT1990__30-1__15-9-19_SR__COMPOSITE__1.LAS`.

**Q3.** К какой скважине и стволу относится лог 15_9-19_SR_CPI.las?  
Ожидание: well `15/9-19 S`, wellbore `15/9-19 SR`.

**Q4.** Какой OSDU ID у ствола 15/9-19 SR?  
Ожидание: `osdu:master-data--Wellbore:NPD-2105`.

**Q5.** Какой OSDU ID у скважины 15/9-19 S?  
Ожидание: `osdu:master-data--Well:15/9-19`.

**Q6.** Какие wellbores у скважины 15/9-F-1?  
Ожидание: `15/9-F-1`, `15/9-F-1 A`, `15/9-F-1 B`, `15/9-F-1 C`.

**Q7.** К какой скважине и стволу относится лог NO_15_9-F-4_KLOGH_NEW.las?  
Ожидание: well `15/9-F-4`, wellbore `15/9-F-4`.

**Q8.** Какой OSDU ID у ствола 15/9-19 A?  
Ожидание: `osdu:master-data--Wellbore:NPD-3145`.

**Q9.** Какие wellbores у скважины 15/9-F-15?  
Ожидание: `15/9-F-15`, `15/9-F-15 A`, `15/9-F-15 B`, `15/9-F-15 C`, `15/9-F-15 D`.

**Q10.** Сколько узлов Well, Wellbore и WellLog в графе?  
Ожидание: Well **11**, Wellbore **27**, WellLog **28**.

**FREE.** Какова стоимость бурения у wellbore 15/9-19 SR (NPD-2105)?  
Ожидание: явный ответ, что свойства/данных о стоимости бурения в графе нет.

## Проверки

- unit: parsing, kind, нормализация, связи, дедупликация;
- integration: повторный load → 0 новых узлов/связей;
- MCP: schema, Q1–Q10, отрицательная write-проверка;
- AppFactory E2E: Q1–Q10 и свободный вопрос без данных в модели
(проверяется финальный текст критика после accept; имена как целые токены,
`SR` ≠ `SR2`; сырой Cypher и REVISE к пользователю не уходят — критик
возвращает черновик аналитику, до 5 правок).

Полный порядок запуска: [INSTALL-LOCAL.md](INSTALL-LOCAL.md).

## Секреты

Credentials в этой папке не хранятся. Loader читает `FALKORDB_URL` из окружения,
bootstrap — временные переменные shell. В логах не печатаются URL с паролями,
JWT и request headers.
