# Выбор MCP / библиотеки — пункт 3.1.4 ТЗ

**Задача:** MVP адаптеров SEG-Y, WITSML и SCADA.  
**Критерий приёмки:** выбор MCP/библиотеки обоснован; зафиксированы версия, лицензия и минимальный поддерживаемый объём каждого формата.  
**Дата сверки первоисточников:** 21 сентября 2026.  
**Правило:** только проверяемые факты (код AppFactory, PyPI JSON, README/SPEC/дерево GitHub, схемы Energistics). Догадки помечены `ASSUMPTION`. Реализация трёх tools в этот документ не входит.

## Оглавление

- [1. Решение](#1-решение)
  - [Минимальный поддерживаемый объём](#минимальный-поддерживаемый-объём)
- [2. Две оси, которые нельзя смешивать](#2-две-оси-которые-нельзя-смешивать)
  - [Ось A — чем разбирать данные](#ось-a--чем-разбирать-данные)
  - [Ось B — как агент вызывает tool](#ось-b--как-агент-вызывает-tool)
- [3. Ограничения платформы AppFactory](#3-ограничения-платформы-AppFactory)
  - [3.1. Stdio в AppFactory есть; для HTTP-образа предпочтителен ZIP](#31-stdio-в-AppFactory-есть-для-http-образа-предпочтителен-zip)
  - [3.2. Контейнер MCP не видит диск агента](#32-контейнер-mcp-не-видит-диск-агента)
  - [3.3. Эталонный стек](#33-эталонный-стек)
  - [3.4. ZIP-preflight](#34-zip-preflight)
- [4. Критерии отсечения (из ТЗ, без добавлений)](#4-критерии-отсечения-из-тз-без-добавлений)
- [5. Готовые MCP](#5-готовые-mcp)
  - [5.1. petroleum-mcp / petromcp 0.8.1 (ameyxd)](#51-petroleum-mcp--petromcp-081-ameyxd)
  - [5.2. petro-mcp 1.1.3 (Groundwork Analytics / petropt)](#52-petro-mcp-113-groundwork-analytics--petropt)
  - [5.3. OpenVDS MCP (`raghujayan/openvds-mcp-server`)](#53-openvds-mcp-raghujayanopenvds-mcp-server)
  - [5.4. OPC UA / промышленная телеметрия (не HTTP API из ТЗ)](#54-opc-ua--промышленная-телеметрия-не-http-api-из-тз)
    - [5.4.1. opcua-mcp-server 0.4.1](#541-opcua-mcp-server-041)
    - [5.4.2. Hermes (AddisonTech)](#542-hermes-addisontech)
    - [5.4.3. i3x2ua (AndreasHeine)](#543-i3x2ua-andreasheine)
    - [5.4.4. kukapay/opcua-mcp](#544-kukapayopcua-mcp)
  - [5.5. DrillerDB (SaaS MCP)](#55-drillerdb-saas-mcp)
  - [5.6. oil-and-gas-claude-skills (ближайший WITSML XML MCP)](#56-oil-and-gas-claude-skills-ближайший-witsml-xml-mcp)
  - [5.7. Выделенный WITSML MCP](#57-выделенный-witsml-mcp)
  - [5.8. Сводка](#58-сводка)
- [6. Библиотеки (после отказа от готового MCP)](#6-библиотеки-после-отказа-от-готового-mcp)
  - [6.1. SEG-Y](#61-seg-y)
  - [6.2. WITSML](#62-witsml)
  - [6.3. SCADA HTTP](#63-scada-http)
- [7. Сборка тонкого адаптера (план, не код)](#7-сборка-тонкого-адаптера-план-не-код)
- [8. Сознательно не выбрано](#8-сознательно-не-выбрано)
- [9. ASSUMPTION](#9-assumption)
- [10. Источники](#10-источники)

---

## 1. Решение

**Готовый MCP-продукт не берём.** Среди публичных серверов, проверенных в этот день, ни один не читает одновременно (и даже по отдельности в срезе ТЗ) три источника: заголовки **бинарного SEG-Y**, один объект **WITSML из локального XML**, снимок через **тестовый SCADA HTTP API**.

**Берём библиотеки + один тонкий MCP-адаптер** по эталону AppFactory `docs/mcp-server-templates/zip/streamable-http`: три read-only tool.

| Слой | Пакет | Версия | Лицензия | Источник |
|---|---|---|---|---|
| MCP-обёртка | `mcp[cli]` (класс `FastMCP` из `mcp.server.fastmcp`) | **1.16.0** | **MIT** | эталон: `docs/mcp-server-templates/README.md:37`, `zip/streamable-http/requirements.txt`; PyPI `mcp` 1.16.0 `license: MIT` |
| Python образа | CPython | **3.11** | PSF | `zip/streamable-http/Dockerfile:1` — `FROM python:3.11-slim` |
| SEG-Y | `segyio` | **1.9.14** | **LGPL-3.0-or-later** | PyPI JSON `license_expression`; wheel `segyio-1.9.14-cp311-cp311-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl` |
| WITSML | `xml.etree.ElementTree` | stdlib 3.11 | PSF | стандартная библиотека CPython 3.11 |
| SCADA HTTP | `httpx` | платформа `>=0.27.0`, пин образа **0.28.1** | **BSD-3-Clause** | `AppFactory/src/requirements.txt:55`; PyPI `httpx` 0.28.1 `license: BSD-3-Clause` |

### Минимальный поддерживаемый объём

| Формат | Версия | Что читаем | Что не читаем | Неподдерживаемая версия |
|---|---|---|---|---|
| SEG-Y | rev **0 и 1** | textual header (3200 байт) + binary header (`f.text` / `f.bin` в segyio) | трассы; SEG-Y 2 / 2.1 как целевой объём | major revision ≥ 2 → `unsupported_version` (отсев **в адаптере**: segyio ревизии сам не различает) |
| WITSML | **1.4.1.1**, объект **`well`** | локальный XML: `uid`, `name`, `field`, `timeZone`, `wellheadElevation` | SOAP Store, 2.x, другие объекты | namespace/version 1.3.1.1 или 2.0/2.1 → `unsupported_version` |
| SCADA | **свой** тестовый HTTP **v1** | `GET /v1/telemetry` | OPC UA, промышленный endpoint, запись | несовместимый путь/версия API → `unsupported_version` |

Поля `well` взяты из схемы **1.4.1.1**, не из обзора 2.0:

- `name` — `minOccurs=1` ([obj_well.html](https://energistics.org/sites/default/files/schema/WITSML_v1.4.1.1_Data_Schema_with_Raster_v1.0/witsml_v1.4.1.1_data/doc/schema/obj_well.html))
- `uid` — `attgrp_objectUid` (тот же файл)
- `field` — опционально ([grp_well.html](https://energistics.org/sites/default/files/schema/WITSML_v1.4.1.1_Data_Schema_with_Raster_v1.0/witsml_v1.4.1.1_data/doc/schema/grp_well.html))
- `timeZone` — `minOccurs=1` (grp_well.html)
- `wellheadElevation` — `minOccurs=0`, тип `witsml:wellElevationCoord` (grp_well.html)

`ASSUMPTION:` ТЗ не называет тип объекта WITSML. Выбран `well`, потому что он есть в схеме 1.4.1.1 как объект скважины. Другой тип меняет fixture/XPath, не стек.

`ASSUMPTION:` контракт JSON для SCADA (`tag`, `value`/`null`, `unit`, `measured_at`, `source`) — это поля **нашего** mock API, не чужой промышленный стандарт.

Синтетика: `.sgy` / `.xml` и mock HTTP отдаёт отдельный сервис `synthetic-data`. Образ MCP их не содержит и читает по `SEGY_BASE_URL`, `WITSML_BASE_URL`, `SCADA_BASE_URL`. Производственные подключения и запись в источники в объём не входят.

---

## 2. Две оси, которые нельзя смешивать

### Ось A — чем разбирать данные

| Вариант | Смысл |
|---|---|
| Готовый MCP | Чужой MCP-сервер регистрируем в AppFactory **как есть** |
| Библиотека + тонкий адаптер | Парсер (PyPI/stdlib) внутри **нашего** маленького MCP |

Это и есть выбор «MCP vs библиотека».

### Ось B — как агент вызывает tool

Это не предмет выбора. Неизвестное имя tool уходит в `agent.execute_tool` (`src/agents/tool_dispatcher.py:219-228`) — путь внешних MCP (`source=mcp_server`).

Builtin-каталог: **26** записей `- _id:` в `src/config/tools.yaml` (от `create` до `ask_human`). Правка ядра — не «внешний инструмент».

Grep `AppFactory/src` по `SEG-Y`, `SEGY`, `WITSML`, `SCADA`, `segyio` → **0 совпадений**. Адаптеров этих форматов в ядре нет.

Эталон ZIP HTTP: `docs/mcp-server-templates/README.md:28-35`.

---

## 3. Ограничения платформы AppFactory

### 3.1. Stdio в AppFactory есть; для HTTP-образа предпочтителен ZIP

Матрица (`docs/mcp-server-templates/README.md:27-35`):

- Remote Discover — публичный/allowlisted URL
- ZIP `streamable-http` и ZIP `stdio` — оба штатные
- Discover Docker Image + stdio — штатный

`localhost` блокируется Discover (`is_blocked_mcp_hostname`, `src/tools/mcp_endpoint_policy.py:125-145`). README шаблонов (`:40-42`) говорит то же про `localhost` / `127.0.0.1` / private IP.

**Неверно** утверждать «stdio MCP в AppFactory запустить нельзя». Есть `docs/mcp-server-templates/zip/stdio/README.md`.

**Верно:** готовый продукт без Dockerfile всё равно нужно упаковать. Упаковка + дописывание форматов — уже не «взять готовый MCP». Для **нашего** адаптера берём `zip/streamable-http`, потому что это документированный предпочтительный путь для HTTP-образа (`README.md:31`, `:46-48`), а не потому что stdio запрещён.

### 3.2. Контейнер MCP не видит диск агента

HTTP: `docker run -d --name --label -p [-e] image` — **нет `-v`** (`src/sandbox/external_mcp_manager.py:338-361`).

Stdio-образ: `docker run --rm -i [--name --label] [-e] image` — **тоже нет `-v`** (`src/tools/external_mcp.py:467-472`).

Следствие: `path` с хоста/Container-Use workdir внутрь MCP не попадёт. Учебные файлы поэтому лежат в отдельном сервисе, а не в образе MCP. Env (`-e`) задаёт `SEGY_BASE_URL`, `WITSML_BASE_URL` и `SCADA_BASE_URL`.

### 3.3. Эталонный стек

Везде в шаблонах: Python 3.11+, `mcp[cli]==1.16.0`, `from mcp.server.fastmcp import FastMCP` (`zip/streamable-http/src/hello_world_mcp/server.py:12`).

Это **другой пакет**, чем PyPI `fastmcp>=2` (зависимость petroleum-mcp и oil-and-gas-claude-skills). Совместимость в одном образе **не доказана** шаблоном. Для нашего адаптера риск снимается: тот же импорт, что эталон.

### 3.4. ZIP-preflight

`src/tools/mcp_dockerfile_preflight.py:78-84` блокирует: `docker.sock`, `--privileged`, `--cap-add`, `SYS_ADMIN`, `nsenter`, `chroot`. Паттерна «`RUN docker`» в этом файле **нет**.

`pip install` + `COPY fixtures/` этим правилам не противоречит.

---

## 4. Критерии отсечения (из ТЗ, без добавлений)

| Код | Критерий |
|---|---|
| К1 | **Сегодня** читает нужный источник, не roadmap |
| К2 | Только чтение; нет tool записи в промышленный источник |
| К3 | Можно зафиксировать версию, лицензию, срез полей |
| К4 | Runtime AppFactory (Python 3.11, ZIP/stdio, без обязательного volume на workdir агента) |
| К5 | Тесты: пусто / битое / неподдерживаемая версия / пропуски / таймаут |
| К6 | Минимальность |

JSON вместо разбора SEG-Y/XML запрещён приёмкой. JSON как тело **HTTP-ответа** mock API — обычный HTTP.

«Well logs» в SaaS ≠ бинарный SEG-Y. «SCADA» в README ≠ тестовый HTTP API ТЗ, если транспорт `opc.tcp`.

---

## 5. Готовые MCP

Ниже только продукты с публичным README и/или PyPI, проверенные 21.09.2026.

### 5.1. petroleum-mcp / petromcp 0.8.1 (ameyxd)

Источники: [PyPI 0.8.1](https://pypi.org/project/petroleum-mcp/0.8.1/), JSON `https://pypi.org/pypi/petroleum-mcp/0.8.1/json`, [GitHub ameyxd/petromcp](https://github.com/ameyxd/petromcp), [SPEC](https://raw.githubusercontent.com/ameyxd/petromcp/main/SPEC_petromcp.md), GitHub API `src/petromcp/tools/`.

| Поле | Факт |
|---|---|
| Версия | **0.8.1**, upload 2026-07-27 |
| Лицензия | MIT (classifier + README) |
| Python | ≥ 3.10 |
| `requires_dist` | `dlisio<1.1,>=1.0.4`, `fastmcp<3,>=2.0`, `lasio<0.32,>=0.31`, numpy, pydantic, pyyaml. **`segyio` нет** |
| Summary PyPI | «Reads LAS well logs and DLIS files; SEG-Y and pump cards next» |
| Транспорт v1 | stdio; Streamable HTTP — v2 (SPEC non-goal 7) |
| Сеть | README: «opens no outbound connections»; tools `openWorldHint: false`, `readOnlyHint: true` |
| Код tools | `__init__.py`, `compare.py`, `dlis.py`, `las.py` — **нет `segy.py`** |

Поставленные tools (таблица README/PyPI): `read_las_file`, `summarize_las_curves`, `read_las_curve`, `compare_well_logs`, `convert_units`, `list_supported_units`, `read_dlis_file`, `list_dlis_channels`, `read_dlis_channel`, prompt `qc_a_well_log`.

SPEC **планирует** `extract_segy_headers` и файл `segy.py`. Это спецификация, не поставка 0.8.1.

WITSML: SPEC non-goal 2 — «WITSML. Real-time streaming is a different problem; defer to v2.»

**Почему кажется подходящим.** Нефтегаз, MIT, read-only, синтетика, SEG-Y headers в SPEC совпадают с MVP.

**Почему не подходит.**

1. **К1 SEG-Y:** нет `segyio` в зависимостях, нет модуля, README: «SEG-Y and pump card support land in subsequent releases».
2. **К1 WITSML:** авторы вынесли из v1.
3. **К1 SCADA:** сеть запрещена — HTTP mock вызвать нельзя.
4. **К4 вторично:** штатный запуск `uvx petroleum-mcp serve`. Stdio ZIP в AppFactory возможен, но другой FastMCP и allowlist хоста в образ не переедут; файлы агента не смонтированы. Даже идеальная упаковка не даёт трёх форматов.

**Вердикт:** не кандидат.

---

### 5.2. petro-mcp 1.1.3 (Groundwork Analytics / petropt)

Не путать с petroleum-mcp.

Источники: [PyPI petro-mcp](https://pypi.org/project/petro-mcp/) (`version` 1.1.3, `license_expression: MIT`, upload 2026-09-06), [GitHub README](https://raw.githubusercontent.com/tallihealth/petro-mcp/main/README.md) (clone URL в README: `github.com/petropt/petro-mcp`).

| Поле | Факт |
|---|---|
| Назначение | «70 petroleum engineering tools» + LAS 2.0 (GitHub README) |
| Транспорт | `petro-mcp # start the MCP server (stdio)` (PyPI description) |
| LAS | Limitations: «LAS 2.0 only» |
| WITSML | Limitations: «No database, WITSML, or API connectors yet» |
| Production | CSV |
| SEG-Y | в группах tools нет (`las`, `production`, `decline`, `pvt`, …) |

**Почему кажется подходящим.** Нефтегаз, MCP, MIT, скважинные файлы.

**Почему не подходит.** Читает LAS, не SEG-Y. WITSML авторы называют отсутствующим. Тестового SCADA HTTP нет. Это калькулятор, не адаптер трёх форматов ТЗ.

**Вердикт:** не кандидат.

---

### 5.3. OpenVDS MCP (`raghujayan/openvds-mcp-server`)

Источник: [README](https://raw.githubusercontent.com/raghujayan/openvds-mcp-server/main/README.md).

| Поле | Факт |
|---|---|
| Формат | OpenVDS / Bluware VDS: `openvds.open()`, `requestVolumeSubset()` |
| Tools | `extract_inline`, `extract_crossline`, `extract_volume_subset`, `get_survey_info`, `list_available_surveys` |
| SEG-Y | Future Enhancements: «Format conversion tools (SEG-Y, etc.)» |
| Demo | «If no VDS files are found, the server runs in demo mode with simulated responses» |
| Лицензия | README: MIT |
| Транспорт | `command: python`, `args: ["src/openvds_mcp_server.py"]` |

**Почему кажется подходящим.** Сейсмика, MCP.

**Почему не подходит.** MVP требует **бинарный SEG-Y**. Сервер читает VDS. SEG-Y в backlog. Demo mode симулирует ответы без файла.

**Вердикт:** не кандидат.

---

### 5.4. OPC UA / промышленная телеметрия (не HTTP API из ТЗ)

ТЗ: «снимок телеметрии через **тестовый SCADA HTTP API**». `opc.tcp://` — другой транспорт.

#### 5.4.1. opcua-mcp-server 0.4.1

- PyPI: [opcua-mcp-server 0.4.1](https://pypi.org/project/opcua-mcp-server/), upload 2026-09-19.
- `license` / `license_expression` в PyPI JSON **пустые**. Лицензия репозитория: GitHub API `IndustriAgents/OPCUA-MCP` (редирект с `midhunxavier/OPCUA-MCP`) — **MIT**.
- `requires_dist`: `mcp[cli]<3,>=2.2.0`, `opcua>=0.98.13`, `httpx>=0.28.1`, `cryptography>=50.0.1`.
- `OPCUA_SERVER_URL` default `opc.tcp://localhost:4840`.
- Summary: «read/write/browse/method/history».
- README примеры: `write_opcua_nodes` («Turn on the main conveyor motor», «Set the pump speed to 65%»).

**Не подходит:** К1 (не HTTP API ТЗ), К2 (write).

#### 5.4.2. Hermes (AddisonTech)

[README](https://raw.githubusercontent.com/AddisonTech/Hermes/main/README.md). GitHub API: лицензия **MIT**.

MCP tools: `read_node`, `read_nodes`, **`write_node`**, `browse_nodes`, `get_server_status`, `get_node_attributes`. Endpoint: `opc.tcp://`. REST `hermes serve`: `GET /nodes`, `GET /nodes/:node_id`, `GET /health` — HTTP **поверх live OPC UA**.

**Не подходит:** К1, К2.

#### 5.4.3. i3x2ua (AndreasHeine)

[README](https://raw.githubusercontent.com/AndreasHeine/i3x2ua/master/README.md). GitHub SPDX `AGPL-3.0`; README: **AGPL-3.0-or-later**.

Шлюз OPC UA → REST + MCP при `I3X_ENABLE_MCP=1`. «PUT routes are intentionally excluded from MCP tool generation». Запись REST при `I3X_ENABLE_WRITES=1`. Нужен внешний OPC UA (`I3X_OPCUA_ENDPOINT`).

**Не подходит:** К1 (источник OPC UA), К6 (шлюз + AGPL ради GET снимка).

#### 5.4.4. kukapay/opcua-mcp

[README](https://raw.githubusercontent.com/kukapay/opcua-mcp/main/README.md): `read_opcua_node`, **`write_opcua_node`**, MIT, stdio, `OPCUA_SERVER_URL`, Python 3.13+.

**Не подходит:** К1, К2.

Зеркало intigration в эту сверку **не открывалось** — не утверждается.

---

### 5.5. DrillerDB (SaaS MCP)

Источник: [drillerdb.com/connectors/mcp](https://drillerdb.com/connectors/mcp).

| Поле | Факт |
|---|---|
| URL | `https://mcp.drillerdb.com`, Streamable HTTP + OAuth 2.0 PKCE |
| Tools | «45 tools: 37 read and 8 write» |
| Данные | проекты, счета, расписание, «well logs» **компании** |
| Это | managed remote production endpoint, не локальный парсер файлов |

**Не подходит:** К1 (не SEG-Y/WITSML XML/тестовый HTTP), К2 (8 write), производческое подключение вне объёма ТЗ.

---

### 5.6. oil-and-gas-claude-skills (ближайший WITSML XML MCP)

Источники: [README](https://raw.githubusercontent.com/ttracx/oil-and-gas-claude-skills/main/README.md), [mcp_server/README.md](https://raw.githubusercontent.com/ttracx/oil-and-gas-claude-skills/main/mcp_server/README.md), GitHub API лицензия **MIT**.

Матрица форматов README: WITSML `.xml` / `xmltodict` / **Beta**. SEG-Y в матрице **нет**. Roadmap: «WITSML 2.0 full parser» не сделан.

MCP tools: `detect_file_type`, `extract_engineering_data`, `parse_las_file`, `parse_drilling_report`, `classify_discipline`, `run_sanity_checks`, `get_skill_definition`. **Нет** `read_witsml_well`.

Транспорт: stdio; HTTP `uvicorn … --port 8342`. Установка MCP: `fastmcp`.

**Почему кажется подходящим.** Единственный найденный публичный MCP, который прямо пишет WITSML XML.

**Почему не подходит.**

1. К1 SEG-Y: формата нет.
2. К1 WITSML: Beta, нет контракта объекта `well` 1.4.1.1.
3. К1 SCADA HTTP: нет.
4. К3: поля WITSML не зафиксированы.
5. К6: PDF/Excel/LAS/DLIS skills — не минимум трёх источников.

**Вердикт:** не берём как готовый продукт.

---

### 5.7. Выделенный WITSML MCP

Поиск `"WITSML" MCP server` + PyPI/GitHub. Отдельного сервера «прочитай `well` 1.4.1.1 из XML» **не найдено**.

Ближайшее, проверенное в эту сверку:

| Что | Почему не адаптер ТЗ |
|---|---|
| petroleum-mcp | WITSML out of v1 (SPEC) |
| petro-mcp | «No … WITSML … connectors yet» |
| oil-and-gas-claude-skills | Beta xmltodict, нет tool объекта `well` |
| jeng 1.0.1 | SOAP-клиент Store, не MCP; есть `add_to_store` / `update_in_store` ([PyPI](https://pypi.org/project/jeng/1.0.1/)) |

---

### 5.8. Сводка

| Продукт | SEG-Y binary headers | WITSML XML well 1.4.1.1 | SCADA HTTP из ТЗ | Read-only | Эталон ZIP HTTP |
|---|---|---|---|---|---|
| petroleum-mcp 0.8.1 | нет | нет | нет (сеть запрещена) | да | нет из коробки |
| petro-mcp 1.1.3 | нет (LAS) | нет | нет (CSV) | калькулятор | нет (stdio) |
| OpenVDS MCP | нет (VDS) | нет | нет | demo симулирует | не эталон |
| opcua-mcp-server 0.4.1 | нет | нет | нет (`opc.tcp`) | **write есть** | нет |
| Hermes | нет | нет | нет (OPC UA) | **`write_node`** | нет |
| i3x2ua | нет | нет | нет (OPC UA→REST) | MCP без PUT | не эталон; AGPL |
| kukapay/opcua-mcp | нет | нет | нет | **`write_opcua_node`** | нет |
| DrillerDB | нет | нет | нет (SaaS) | **8 write** | remote SaaS |
| oil-and-gas-claude-skills | нет | Beta, не `well` 1.4.1.1 | нет | skills читают файлы | `fastmcp` |

Три готовых MCP рядом не складываются: нет поставленного SEG-Y headers MCP, WITSML не зафиксирован, SCADA-кандидаты — другой протокол и часто пишут.

---

## 6. Библиотеки (после отказа от готового MCP)

Тонкий адаптер — **наш** MCP по шаблону AppFactory.

### 6.1. SEG-Y

segyio README: textual header — «3200-byte byte-like blobs»; binary header — `f.bin`. Ссылки на стандарты: [SEG-Y 0](https://seg.org/wp-content/uploads/2025/11/seg_y_rev0.pdf), [SEG-Y 1](https://seg.org/wp-content/uploads/2025/11/seg_y_rev1.pdf). Feature summary: «Read and write binary and textual headers».

| Кандидат | Версия | Лицензия | Зачем / почему нет |
|---|---|---|---|
| **segyio** | 1.9.14 | LGPL-3.0-or-later | Целевой парсер; wheel cp311 manylinux |
| segysak | 0.5.4 | GPL-3.0 (classifier GPLv3); `requires_dist` включает `segyio` | Обёртка + dask/matplotlib/scipy. Сильнее copyleft. Не минимум |
| ObsPy | 1.5.1 | LGPLv3 | numpy/scipy/matplotlib ради заголовка |
| свой `struct` | — | — | EBCDIC/endian писать самим |

segyio README: «does not discriminate between the revisions». Отсев rev ≥ 2 — в адаптере. Runtime: `segyio.open(path, "r", …)` — только чтение. Генерация fixture — не tool.

**Выбор: segyio 1.9.14, read-only.** Имена полей binary header — `segyio.BinField` при реализации (не выдуманы в этом документе списком байт).

### 6.2. WITSML

| Кандидат | Версия | Лицензия | Почему не минимум |
|---|---|---|---|
| jeng | 1.0.1 | MIT | SOAP Store; `add_to_store` / `update_in_store` |
| komle-witslm-client | 0.3.4.2 | Apache-2.0 | схемы 1.3.1.1–2.0, «just one version … same runtime»; `PyXB-X==1.2.6`. Пакет `komle` на PyPI **404** |
| energyml-witsml2-0 | 1.12.0 | Apache-2.0 | только 2.0 |
| xmltodict (как в skills) | — | — | не схема 1.4.1.1 |
| **ElementTree** | stdlib | PSF | ноль зависимостей; версию ловим по `xmlns` |

**Выбор: ElementTree, WITSML 1.4.1.1 `well`.**

Исходы: нет `well` → `empty`; elevation без `uom` → `incomplete` (не `0`); битый XML → `corrupt_input`.

### 6.3. SCADA HTTP

Готового MCP под тестовый HTTP API нет (§5.4).

| Кандидат | Лицензия | Зачем |
|---|---|---|
| urllib | PSF | ноль зависимостей, timeout вручную |
| **httpx** | BSD-3-Clause | `timeout=`; уже в `src/requirements.txt:55` |

Платформа помечает MCP timeout как `reason: timeout` (`src/tools/mcp_executor.py:50-51`).

Tool обязан сделать HTTP GET. Тело mock может быть JSON. В tool list нет POST/PUT/PATCH/DELETE.

**Выбор: httpx 0.28.1. Mock HTTP v1 работает в сервисе `synthetic-data`, не в образе адаптера.**

---

## 7. Сборка тонкого адаптера (план, не код)

Один ZIP, три tool, один lifecycle.

```
GenericAgent.allowed_mcp_tools:
  <server>.read_segy_headers
  <server>.read_witsml_well
  <server>.read_scada_snapshot
        → streamable-http :8080/mcp
FastMCP (mcp[cli]==1.16.0, python:3.11-slim)
  SEGY_BASE_URL (-e)    → GET file → segyio.open(..., "r")
  WITSML_BASE_URL (-e)  → GET file → ElementTree
  SCADA_BASE_URL (-e)   → httpx.get
```

Публичный id `server.tool`: `src/tools/agent_allowed_tools.py:88-90`.

`ASSUMPTION:` общий JSON `{status, reason, format, format_version, source, data, missing_fields}` — контракт адаптера для единообразных тестов, не поле чужого MCP.

---

## 8. Сознательно не выбрано

| Идея | Почему |
|---|---|
| Fork petroleum-mcp | Нет WITSML и HTTP; другой FastMCP |
| oil-and-gas-claude-skills «хотя бы для WITSML» | Beta, нет контракта `well`, нет SEG-Y/HTTP |
| Три готовых MCP | Не из чего собрать SEG-Y headers и зафиксированный WITSML |
| Builtin в `tools.yaml` | Не внешний инструмент |
| Remote mock на localhost | SSRF Discover |
| komle-witslm-client «на всякий случай» | PyXB и одна версия схемы на runtime |
| OPC UA / Hermes / i3x2ua | Другой протокол, часто write |
| DrillerDB | SaaS, write, не те форматы |

---

## 9. ASSUMPTION

- Сборка ZIP имеет исходящий интернет для `pip install` (preflight не регламентирует).
- `python:3.11-slim` ставит wheel `manylinux_2_28` — вывод из тега wheel, не из `docker build` в этом документе.
- LGPL segyio vs политика организации — фиксация лицензии, не юрзаключение.
- Тип WITSML `well` — предложение: ТЗ тип не назвал.
- Полнота поиска: публичные PyPI/GitHub/страницы продуктов. Закрытый корпоративный MCP вне этой сверки не опровергнут и не подтверждён.
- IoT/Modbus MCP в эту сверку отдельным успешным GitHub Contents API не фиксировался — в таблицу не включён, чтобы не опираться на прошлый обход 404.

---

## 10. Источники

**AppFactory:** `docs/mcp-server-templates/README.md`; `zip/streamable-http/` (Dockerfile, requirements.txt, `server.py`); `zip/stdio/README.md`; `src/sandbox/external_mcp_manager.py`; `src/tools/external_mcp.py`; `src/tools/mcp_endpoint_policy.py`; `src/tools/mcp_dockerfile_preflight.py`; `src/agents/tool_dispatcher.py`; `src/tools/mcp_executor.py`; `src/tools/agent_allowed_tools.py`; `src/config/tools.yaml`; `src/requirements.txt`.

**MCP:**  
https://pypi.org/pypi/petroleum-mcp/0.8.1/json · https://github.com/ameyxd/petromcp · https://raw.githubusercontent.com/ameyxd/petromcp/main/SPEC_petromcp.md · https://pypi.org/pypi/petro-mcp/json · https://raw.githubusercontent.com/tallihealth/petro-mcp/main/README.md · https://raw.githubusercontent.com/raghujayan/openvds-mcp-server/main/README.md · https://pypi.org/pypi/opcua-mcp-server/json · https://github.com/IndustriAgents/OPCUA-MCP · https://raw.githubusercontent.com/AddisonTech/Hermes/main/README.md · https://github.com/AndreasHeine/i3x2ua · https://raw.githubusercontent.com/kukapay/opcua-mcp/main/README.md · https://drillerdb.com/connectors/mcp · https://github.com/ttracx/oil-and-gas-claude-skills · https://pypi.org/pypi/mcp/1.16.0/json

**Библиотеки / схемы:**  
https://pypi.org/pypi/segyio/json · https://raw.githubusercontent.com/equinor/segyio/master/README.md · https://pypi.org/pypi/httpx/json · https://pypi.org/pypi/segysak/json · https://pypi.org/pypi/obspy/json · https://pypi.org/pypi/jeng/1.0.1/json · https://pypi.org/pypi/komle-witslm-client/0.3.4.2/json · https://pypi.org/pypi/energyml-witsml2-0/json · https://energistics.org/sites/default/files/schema/WITSML_v1.4.1.1_Data_Schema_with_Raster_v1.0/witsml_v1.4.1.1_data/doc/schema/obj_well.html · https://energistics.org/sites/default/files/schema/WITSML_v1.4.1.1_Data_Schema_with_Raster_v1.0/witsml_v1.4.1.1_data/doc/schema/grp_well.html
