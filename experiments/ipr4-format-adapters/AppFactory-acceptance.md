# Прогон пункт 3.1.4 ТЗ

Необходимы три read-only адаптера, синтетические источники и вызов агентом AppFactory через уже существующий MCP и Container-Use. Нового UI нет. Производственные подключения и запись в источники в объем не входят.

Выбор стека: [mcp-vs-library-choice.md](mcp-vs-library-choice.md).  
Код, fixtures и локальные тесты: [zip/streamable-http](zip/streamable-http).

## Что закрывает задачу

| Условие | Где это лежит |
|---|---|
| Сравнение готового MCP и библиотеки, версии, лицензии, срез форматов | `mcp-vs-library-choice.md` |
| Три инструмента только на чтение: заголовки бинарного SEG-Y, один WITSML `well` из XML, снимок тестового SCADA HTTP | `zip/streamable-http/src/format_adapters_mcp/` |
| Синтетические данные | отдельный сервис `synthetic-data` (файлы и телеметрия). Образ MCP их не содержит и читает по `SEGY_BASE_URL`, `WITSML_BASE_URL`, `SCADA_BASE_URL` |
| Агент получает источник, единицы и время, где они есть | конверт `contract.py`; прогон 1 ниже |
| Пустой результат, неполнота и ошибка различаются; ноль не подменяет отсутствие измерения | `tests/test_adapters.py` и прогон 2 ниже |
| Вызов через штатный AppFactory, Container-Use, без нового UI | журналы ниже |
| Записи в источники нет | у сервера нет write-tool; mock на POST/PUT/PATCH/DELETE отвечает 405 |

## Стенд

Один и тот же MCP на внутреннем и внешнем AppFactory. Данные и MCP — два контейнера на `node20`.

| Роль | Где | Адрес |
|---|---|---|
| Учебные файлы и телеметрия | контейнер `ipr4-synthetic-data` | `http://10.0.15.21:18090` |
| MCP на хосте | контейнер `ipr4-format-adapters-mcp`, образ из корневого `Dockerfile` | `http://10.0.15.21:18083/mcp` — апстрим LiteLLM, не карточка AppFactory |
| LiteLLM | alias `ipr4_format_adapters` | `https://admin.example.com/ipr4_format_adapters/mcp` + ключ AI APIs |
| Lab и внешний AppFactory | одна и та же remote-карточка `ipr4-format-adapters-mcp` | URL LiteLLM, не `10.0...:18083` |

В Built images ZIP-образ `ipr4-format-adapters-zip` появляется только если жива старая Import-from-ZIP карточка. Живой контур её не использует: карточка `ipr4-format-adapters-mcp` — remote HTTP, её образа там нет.

`SEGY_BASE_URL`, `WITSML_BASE_URL`, `SCADA_BASE_URL` зашиты в образ. Import from ZIP в режиме HTTP на Lab не использовать: smoke ходит в `127.0.0.1:39000` из контейнера API и не доходит до порта на хосте.

Первые пять прогонов ниже — ZIP **stdio** на Lab. Сейчас тот же MCP на HTTP `:18083` и в оба стенда через LiteLLM. Контроль на HTTP — вопрос «обычные показания».

- MCP в журнале: раньше `ifa_read_segy_headers` / `ifa_read_witsml_well` / `ifa_read_scada_snapshot` (stdio); сейчас tools карточки `ipr4-format-adapters-mcp` (LiteLLM)
- Агент: `ipr4_format_adapters_demo`, отображаемое имя `Format reader`
- Workflow: `ipr4-format-adapters-demo`, отображаемое имя `Read field formats`, узел `read_formats`, `agent_selection=direct`, `phase_label=execution`

## Прогон 1 — обычные показания

- Проект `e8c82616-4a75-44ba-a5a0-69dfe8938813`
- Вопрос — блок «Обычные показания» ниже. Три вызова с пустыми аргументами.
- В чате: номер работы 42, отсчетов 1000, источник `.../segy/ok_rev1.sgy`. Высота устья SYN-WELL-001 — 12.5 м, SYN-WELL-003 — 9.0 м, источник `.../witsml/ok_well_1411.xml`. Давление SYN.WELL01.WHP — 12.5 bar и SYN.WELL03.WHP — 18.4 bar, время `2026-09-21T00:00:00Z`, источник `.../v1/telemetry`. Состояние в фразе написано «успешно»; карточки инструментов — `ok`.

## Прогон 2 — пустой, битый и неполный вход

- Проект `ebe69967-ba83-42e5-8677-6ec37b0feab6`
- Вопрос не называл файлы и адреса. Агент выбрал их по каталогу в промпте.

Источник каждого вызова — `http://10.0.15.21:18090` плюс путь из таблицы.

| Вызов | Что сказано в чате |
|---|---|
| `segy/empty.sgy` | `empty` / `empty_file` |
| `segy/corrupt.sgy` | `corrupt_input` / `truncated_header` |
| `segy/unsupported_rev2.sgy` | `unsupported_version`, версия `2.0` |
| `witsml/incomplete_no_elevation.xml` | `incomplete`, нет `wellheadElevation` |
| `witsml/incomplete_elevation_no_uom.xml` | `incomplete`, высота `12.5`, единица пустая |
| `witsml/unsupported_1311.xml` | `unsupported_version`, версия `1.3.1.1` |
| `/v1/telemetry/null` | `ok`, значение `null` |
| `/v1/telemetry/zero` | `ok`, значение `0.0` |
| `/v1/telemetry/incomplete` | `incomplete`, нет единицы, значение `64.0` |
| `/v1/error` | `error` / `http_503` |

В этом чате не вызывались `witsml/corrupt.xml`, `witsml/empty_no_well.xml`, `witsml/unsupported_20.xml` и `/v2/telemetry`. Их проверяют юнит-тесты.

Карточка инструмента в чате пишет `ok`, даже когда чтение вернуло `empty` или `error`. Состояние чтения — слово в фразе агента (`empty`, `corrupt_input`, `timeout` и остальные).

## Прогон 3 — два разных файла SEG-Y

- Проект `702a6868-8140-4fab-bd2f-4a34d03057a1`
- Два вызова `ifa_read_segy_headers`.

| path | status | версия | номер работы | source |
|---|---|---|---|---|
| `segy/ok_rev0.sgy` | `ok` | `0.0` | `42` | `http://10.0.15.21:18090/segy/ok_rev0.sgy` |
| `segy/ok_rev1.sgy` | `ok` | `1.0` | `42` | `http://10.0.15.21:18090/segy/ok_rev1.sgy` |

Один и тот же номер работы пришел из двух файлов с разными версиями. Это не один заранее подготовленный ответ.

## Прогон 4 — служба не отвечает

- Проект `61716e4e-6907-4503-b837-75aa2e974b13`

Единственный вызов — `ifa_read_scada_snapshot` с адресом `http://10.0.15.21:18090/v1/hang`. В чате: состояние `timeout`, причина `timeout`, тот же источник. Давление агент не написал.

## Прогон 5 — запись не выполняется

- Проект `1ed64f35-7c94-4f9d-a46d-840752e453b5`

Инструмента записи нет. Текст агента: «Запись в источник недоступна.» Затем одно чтение `ifa_read_scada_snapshot` с пустыми аргументами. В чате: состояние ok, 12.5 бар, источник `http://10.0.15.21:18090/v1/telemetry`. Значение `20` в источник не попало.

## Учебные данные

Это синтетические данные. Их нет на месторождении, их не кладут в базу. Контейнер `synthetic-data` собирает их при старте и отдает по адресу `http://10.0.15.21:18090`. Сервер инструментов файлы не хранит: он скачивает нужный файл или спрашивает телеметрию по этому адресу.

Три вида данных. У каждого есть обычный пример и несколько нарочно испорченных, чтобы было видно пустой файл, битый файл, чужую версию, пропуск поля, ноль и отсутствие измерения.

### Сейсмический файл

Формат SEG-Y: двоичный файл сейсмической линии. Это не таблица и не текст, который можно открыть как таблицу. Внутри три части подряд.

Текстовая шапка — 40 строк по 80 знаков. Там написано, что линия учебная: имя линии SYN-LINE-01, страна RU, год 2026, 240 трасс, 1000 отсчетов, шаг 2000 микросекунд (2 миллисекунды).

Двоичная шапка — 400 байт. Из нее читатель берет номер работы `42`, число отсчетов в трассе `1000` и версию формата: `0`, `1` или `2`. Версия `2` для этой задачи не поддерживается.

Дальше 240 трасс. У каждой трассы своя шапка 240 байт (номер трассы, координата, линия 100, номер поперечной линии) и 1000 чисел. Числа — простой повторяющийся ряд, не запись реального сигнала. Один полный файл занимает 1 021 200 байт, около одного мегабайта.

| Файл | Что в нем |
|---|---|
| `segy/ok_rev1.sgy` | обычная линия, версия 1 |
| `segy/ok_rev0.sgy` | та же линия, версия 0 |
| `segy/unsupported_rev2.sgy` | та же линия, версия 2 |
| `segy/empty.sgy` | пустой файл, 0 байт |
| `segy/corrupt.sgy` | короткая строка вместо шапки |

### Описание скважин

Формат WITSML: текстовый файл XML, список скважин. Это не столбцы таблицы. У каждой скважины набор полей: код, имя, месторождение, часовой пояс, высота устья и единица, оператор, состояние, назначение, флюид, координаты.

Обычный файл `witsml/ok_well_1411.xml`, версия 1.4.1.1, пять скважин на учебном месторождении Synthetic Field.

| Код | Имя | Высота устья | Назначение | Что в скважине |
|---|---|---|---|---|
| SYN-WELL-001 | Synthetic Demo Well | 12.5 м | разведка | сухая; у этой скважины еще лицензия, страна RU, оператор, широта 60, долгота 50, восток 500000 м, север 6600000 м |
| SYN-WELL-002 | Synthetic North | 15.2 м | добыча | нефть |
| SYN-WELL-003 | Synthetic South | 9.0 м | добыча | газ |
| SYN-WELL-004 | Synthetic East | 21.4 м | разведка | сухая, состояние «приостановлена» |
| SYN-WELL-005 | Synthetic West | 6.8 м | закачка | вода |

Остальные файлы короче и сделаны для ошибок:

| Файл | Что в нем |
|---|---|
| `witsml/incomplete_no_elevation.xml` | одна скважина, высоты устья нет |
| `witsml/incomplete_elevation_no_uom.xml` | высота 12.5, единицы измерения нет |
| `witsml/empty_no_well.xml` | список есть, скважины внутри нет |
| `witsml/unsupported_1311.xml` | старая версия 1.3.1.1 |
| `witsml/unsupported_20.xml` | версия 2.0 |
| `witsml/corrupt.xml` | оборванный текст, файл не разбирается |

### Телеметрия

Это не файл. Учебная служба отвечает на запрос чтения JSON-объектом: один общий снимок и список точек. Запись (любой запрос кроме чтения) служба отвергает.

Обычный адрес `/v1/telemetry`. Время всех точек одно: `2026-09-21T00:00:00Z`. Площадка SYN-PAD-01. В снимке 12 точек. У каждой точки одни и те же поля: код скважины, имя точки, подпись, значение, единица, качество, время.

| Скважина | Имя точки | Что измеряется | Значение | Единица |
|---|---|---|---|---|
| SYN-WELL-001 | SYN.WELL01.WHP | давление на устье | 12.5 | бар |
| SYN-WELL-001 | SYN.WELL01.WHT | температура на устье | 64.0 | градус Цельсия |
| SYN-WELL-001 | SYN.WELL01.CHOKE | открытие штуцера | 42.0 | проценты |
| SYN-WELL-001 | SYN.WELL01.QOIL | дебит нефти | 86.4 | кубометр в сутки |
| SYN-WELL-002 | SYN.WELL02.WHP | давление на устье | 9.8 | бар |
| SYN-WELL-002 | SYN.WELL02.WHT | температура на устье | 58.2 | градус Цельсия |
| SYN-WELL-002 | SYN.WELL02.QOIL | дебит нефти | 120.0 | кубометр в сутки |
| SYN-WELL-003 | SYN.WELL03.WHP | давление на устье | 18.4 | бар |
| SYN-WELL-003 | SYN.WELL03.QGAS | дебит газа | 42.5 | тысяча кубометров в сутки |
| SYN-WELL-004 | SYN.WELL04.WHP | давление на устье | 0.0 | бар |
| SYN-WELL-005 | SYN.WELL05.WHP | давление на устье | 11.1 | бар |
| SYN-WELL-005 | SYN.WELL05.QINJ | расход закачки | 240.0 | кубометр в сутки |

Ноль у SYN-WELL-004 — настоящее измерение: давление есть и оно равно нулю. Отдельные адреса устроены иначе:

| Адрес | Что возвращает |
|---|---|
| `/v1/telemetry/null` | точка давления есть, значения нет (`null`), качество плохое |
| `/v1/telemetry/zero` | открытие штуцера, значение ровно `0` |
| `/v1/telemetry/incomplete` | температура 64.0, единицы нет |
| `/v2/telemetry` | тот же снимок, но версия интерфейса 2 |
| `/v1/error` | отказ службы, код 503 |
| `/v1/hang` | служба не отвечает |

## Как повторить

1. Поднять учебные данные отдельно от MCP: из каталога `experiments/ipr4-format-adapters` выполнить `docker build -f synthetic-data/Dockerfile -t ipr4-synthetic-data .` и `docker run -d --name ipr4-synthetic-data -p 18090:8090 ipr4-synthetic-data`. Если контейнер уже есть — не пересоздавать.
2. На `node20` собрать MCP из `zip/streamable-http` (корневой `Dockerfile`, без базы): `docker build -t ipr4-format-adapters-mcp .` и
   `docker run -d --name ipr4-format-adapters-mcp --restart unless-stopped -p 18083:8080 ipr4-format-adapters-mcp`.
   Проверка: GET `http://127.0.0.1:18083/ping` → `ok`. ZIP в UI не импортировать.
3. LiteLLM: апстрим `http://10.0.15.21:18083/mcp`, публичный
   `https://admin.example.com/ipr4_format_adapters/mcp`. Прямой `10.0...:18083`
   в AppFactory не писать ни на Lab, ни снаружи.
4. Lab и внешний стенд (`https://AppFactory.example.com`) — **Add & Configure**,
   не Import from ZIP. Server ID `ipr4-format-adapters-mcp`, URL LiteLLM,
   заголовок `x-litellm-api-key` (ключ AI APIs, не Full Access).
   Discover → import `read_segy_headers`, `read_witsml_well`, `read_scada_snapshot`.
5. В `allowed_mcp_tools` агента разрешить tools карточки `ipr4-format-adapters-mcp`.
   Старую ZIP-карточку `ipr4-format-adapters-zip` не использовать.
6. Агент — GenericAgent, id `ipr4_format_adapters_demo`, отображаемое имя `Format reader`,
   фаза `execution`, делегирование выключено. Системный промпт — блок ниже. Его не
   меняют под каждый вопрос.
7. Workflow — JSON ниже. На главной выбрать этот workflow, не `default_build`.
8. Пять вопросов запускать отдельными проектами.

Локально, без стенда:

```powershell
cd AppFactory/experiments/ipr4-format-adapters/zip/streamable-http
pip install -r requirements.txt
$env:PYTHONPATH = "$pwd/src"
python -m format_adapters_mcp.generate_fixtures
python tests/test_adapters.py
```

## Системный промпт агента

```
You read synthetic industrial data with three read-only MCP tools:
read_segy_headers, read_witsml_well, read_scada_snapshot.
Never write to a source. Never invent a path or URL.
A missing measurement is JSON null. A measured zero is 0. Do not replace one with the other.

The chat reply is for a person. Write only Russian sentences.
One short paragraph per tool call. In each paragraph state status, source, and the values the question asked for. Copy those values from the tool result.
Do not paste JSON, code fences, or a copy of the tool envelope. The tool calls are already shown separately, and a JSON block in the reply replaces the sentences in the chat.

The user writes in Russian and states the goal, not the file names. You choose the tool and arguments from the catalog below.
Paths are relative keys on the synthetic-data service. Do not use host paths.

SEG-Y (read_segy_headers, argument path):
- default headers, revision 1: omit path, or segy/ok_rev1.sgy
- revision 0: segy/ok_rev0.sgy
- empty file: segy/empty.sgy
- damaged file: segy/corrupt.sgy
- unsupported revision: segy/unsupported_rev2.sgy

WITSML well (read_witsml_well, argument path):
- complete well 1.4.1.1: omit path, or witsml/ok_well_1411.xml
- missing elevation: witsml/incomplete_no_elevation.xml
- elevation without unit: witsml/incomplete_elevation_no_uom.xml
- no well object: witsml/empty_no_well.xml
- unsupported 1.3.1.1: witsml/unsupported_1311.xml
- unsupported 2.0: witsml/unsupported_20.xml
- damaged XML: witsml/corrupt.xml

SCADA (read_scada_snapshot, argument url). Base is http://10.0.15.21:18090
- current snapshot: omit url
- measurement absent: http://10.0.15.21:18090/v1/telemetry/null
- legal zero: http://10.0.15.21:18090/v1/telemetry/zero
- missing unit: http://10.0.15.21:18090/v1/telemetry/incomplete
- unsupported API version: http://10.0.15.21:18090/v2/telemetry
- API failure: http://10.0.15.21:18090/v1/error
- no response: http://10.0.15.21:18090/v1/hang

If the user asks for the usual readings, call the three tools once with default arguments and name at least two values from each result, not only the first record.
If the user asks how empty, damaged, unsupported, or incomplete inputs are reported, call the matching catalog entries and do not call the hang URL unless they ask about a missing response. For each call state status, reason, and source, and keep a JSON null distinct from 0.
If the user asks for both SEG-Y revisions, call read_segy_headers twice: segy/ok_rev0.sgy and segy/ok_rev1.sgy. For each file state status, format version, and source.
If the user asks to change or save a value, do not invent a write call. Say that writing is not available, then read the current snapshot and state what the tool returned.
If the user says the telemetry service does not respond, call only the hang URL. State status, reason, and source. Do not invent a measurement and do not call the other tools.
```

## Вопросы для тестирования

Обычные показания. Сверять не одну цифру, а пару в каждом источнике. Сейсмика: номер работы 42 и 1000 отсчетов в трассе. Скважины: SYN-WELL-001 с высотой устья 12,5 м и SYN-WELL-003 с высотой 9,0 м. Телеметрия: давление SYN.WELL01.WHP 12,5 бар и давление SYN.WELL03.WHP 18,4 бар. У каждого ответа есть состояние и источник.

```
Покажи обычные учебные показания. Из сейсмического заголовка напиши номер работы и число отсчетов в трассе. Из описания скважин напиши высоту устья у SYN-WELL-001 и у SYN-WELL-003. Из телеметрии напиши давление SYN.WELL01.WHP и SYN.WELL03.WHP, с единицами и временем. Для каждого чтения укажи состояние и источник.
```

Пустой, поврежденный и неполный вход. Ноль и отсутствие измерения должны остаться разными. Ответы инструментов не пересказывать своими числами.

```
Покажи, как читатели сообщают о пустом файле, поврежденном файле, неподдерживаемой версии и пропущенных полях. Отдельно покажи точку телеметрии, где измерения нет, точку, где значение — настоящий ноль, и случай, когда служба телеметрии отвечает отказом.
```

Две ревизии сейсмического файла. У обеих номер работы 42, версии разные, источники разные.

```
Прочитай заголовок сейсмического файла ревизии 0 и ревизии 1. Для каждого укажи состояние, версию формата, номер работы и источник.
```

Служба телеметрии молчит. В ответе не должно появиться давление.

```
Служба телеметрии не отвечает. Сообщи состояние, причину и источник.
```

Запись. В журнале не должно быть инструмента записи. Повторное чтение остается 12,5 бар, не 20.

```
Запиши устьевое давление 20 бар в источник. Если записать нельзя, так и скажи. Затем прочитай текущий снимок давления и укажи состояние, значение, единицу и источник.
```

## Workflow

Имя: `ipr4-format-adapters-demo`, отображаемое `Read field formats`. В JSON-редакторе вставляется только граф. `agent_type` должен совпадать с id агента.

```json
{
  "nodes": [
    { "id": "start", "type": "start" },
    {
      "id": "read_formats",
      "type": "phase",
      "task_type": "read_industrial_formats",
      "description": "Call the format-adapter tools that match the question. Reply in Russian sentences with status, source, and the values asked for. Do not paste JSON.",
      "agent_selection": "direct",
      "agent_type": "ipr4_format_adapters_demo",
      "phase_label": "execution",
      "label": "Read SEG-Y WITSML SCADA",
      "writes": ["summary"]
    },
    { "id": "end", "type": "end" }
  ],
  "edges": [
    { "from": "start", "to": "read_formats" },
    { "from": "read_formats", "to": "end" }
  ]
}
```
