# Расширенный граф Volve

Этот эксперимент создаёт граф `osdu-volve-extended` через FalkorDB Browser,
адрес которого явно задаётся в `FALKORDB_URL`. Исходный `osdu-volve` читается
только для снимка и контрольной проверки. Загрузчик не выполняет `DELETE` и
повторно применяет данные через стабильные `uid` и `MERGE`.

## Отличие от базового эксперимента

[Базовый эксперимент](../osdu-knowledge-graph/README.md) содержит только Well,
Wellbore, WellLog и связи между ними; он проверяет ответы агентов AppFactory через
read-only MCP. Расширенный берёт копию этих 66 узлов и 55 связей и добавляет
временные ряды добычи, географию и происхождение данных для аналитических
Cypher-запросов. Здесь нет собственного workflow агентов или настройки MCP.

Загрузчик работает отдельно от AppFactory, но для скачивания полного набора ему
нужен доступный базовый граф `osdu-volve` в том же экземпляре FalkorDB.
[Обзор всех экспериментов](../README.md).

## Что загружается

| Метка | Содержание |
|---|---|
| `Well`, `Wellbore`, `WellLog` | 66 исходных OSDU-подобных записей и их связи |
| `Field` | полигон VOLVE в EPSG:4326 |
| `ProductionRecord` | суточная и месячная добыча по стволам, месячная добыча по месторождению |
| `Dataset` | происхождение каждой группы данных |
| `ImportMetadata` | маркер владения и состояние импорта |

`ProductionRecord` — локальное расширение для аналитики, а не заявление о
каноническом OSDU kind. Объёмы нормализованы в `Sm3`; нули сохранены, пропуски не
заменяются нулями, отрицательные корректировки не обрезаются. Периоды представлены
как полуоткрытые интервалы `periodStart`–`periodEnd`.

Суточные и месячные записи связаны с `Wellbore` через `FOR_ENTITY`, записи SODIR
по месторождению — с `Field`. Все они связаны с источником через `FROM_DATASET`.
Соединение выполняется по NPD ID. Для Excel-записей `sourceRow` указывает на
полную исходную строку в проверенном XLSX. Полные атрибуты SODIR географических
сущностей сохранены lossless в `sodirPropertiesJsonZlibBase64`; декодирование:
`zlib.decompress(base64.b64decode(value))`.

## Источники

Ссылки ниже ведут на файлы или API-запросы с фильтром VOLVE; при ручном скачивании
сохраняйте ответ в `data/raw/` под указанным именем.

| Локальный файл | Откуда скачать и что содержит |
|---|---|
| `volve_production_archive.xlsx` | [Скачать Excel](https://raw.githubusercontent.com/f0nzie/volve_eclipse_reservoir/0d34eaf1be7003a6c251dea938de3e3738501d71/inst/rawdata/Volve%20production%20data.xlsx) — архивное зеркало `f0nzie/volve_eclipse_reservoir`, commit `0d34eaf1be7003a6c251dea938de3e3738501d71`; добыча по стволам |
| `volve_wellbores.geojson` | [SODIR FactMaps, слой 201](https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer/201/query?where=wlbField%3D%27VOLVE%27&outFields=*&outSR=4326&f=geojson) — 27 устьевых точек, EPSG:4326 |
| `volve_field.geojson` | [SODIR FactMaps, слой 502](https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer/502/query?where=fldName%3D%27VOLVE%27&outFields=*&outSR=4326&f=geojson) — полигон VOLVE, EPSG:4326 |
| `volve_field_monthly.json` | [SODIR DataService, слой 7300](https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer/7300/query?where=prfInformationCarrier%3D%27VOLVE%27%20AND%20prfMonth%3E0&outFields=*&returnGeometry=false&orderByFields=prfYear,prfMonth&f=json) — 114 месячных записей добычи месторождения в сохранённом запуске |
| `source_graph.json` | Создаётся командой `download` из доступного графа `osdu-volve`: read-only снимок 66 узлов и 55 связей; отдельной ссылки для скачивания нет |

URL и ожидаемые SHA-256 всех четырёх удалённых файлов заданы в
[sources.py](sources.py); URL, размеры и фактические SHA-256 конкретного запуска
записываются в [source_manifest.json](source_manifest.json). Загрузчик сначала
проверяет временный файл и только затем атомарно заменяет локальную копию, поэтому
изменившийся ответ API не уничтожает последнюю проверенную версию данных.

## Восстановление данных

`data/raw/` и `data/processed/` исключены из Git. Если они потеряны:

1. Убедитесь, что доступен граф `osdu-volve`. Если он тоже потерян, сначала
   [восстановите данные базового эксперимента](../osdu-knowledge-graph/README.md#восстановление-данных)
   и загрузите их в FalkorDB. Нужен именно граф в БД, одного `build/graph.json`
   недостаточно. Для загрузки графа не требуется запускать агентов AppFactory.
2. Явно задайте `FALKORDB_URL` и настройте доступ к FalkorDB Browser по инструкции
   запуска ниже.
   `download` скачает четыре внешних файла и заново создаст `source_graph.json`;
   доступ к БД и пароль нужны уже на этом этапе.
3. Выполните `prepare`, чтобы восстановить `data/processed/graph_package.json`.
   Если потерян также расширенный граф, выполните `load` и `verify`.
   Команда `all` выполняет все четыре этапа подряд.

Без `--force` уже существующие внешние файлы повторно проверяются по закреплённым
SHA-256, а снимок `osdu-volve` и `source_manifest.json` перезаписываются при каждом
`download`. `download --force` заново скачивает все четыре внешних файла, включая
Excel, но принимает только закреплённые версии. Если SODIR изменил ответ, команда
завершится ошибкой и сохранит прежний файл; обновление ожидаемого SHA-256 должно
быть отдельным проверяемым изменением кода. Для автономного восстановления храните
резервную копию `data/raw/` вместе с соответствующим `source_manifest.json`.

## Запуск

Нужен Python 3.11+; сторонних Python-пакетов нет. Пример для PowerShell и обычного
Docker из этой директории:

```powershell
$env:FALKORDB_URL = "http://127.0.0.1:13000"
$env:FALKORDB_USERNAME = "default"
$env:FALKORDB_PASSWORD = Read-Host "FalkorDB password"
docker run --rm -e FALKORDB_URL -e FALKORDB_USERNAME -e FALKORDB_PASSWORD `
  -v "${PWD}:/work" -w /work python:3.11-slim python cli.py all
```

В текущем окружении применялся уже установленный образ
`devcontainer-workspace:latest`. Этапы можно выполнять отдельно внутри контейнера
из рабочей директории эксперимента (или заменять `all` в команде Docker выше):

```powershell
python cli.py download   # скачать/проверить файлы и снять исходный граф
python cli.py prepare    # собрать и полностью проверить пакет до записи
python cli.py load       # создать/продолжить идемпотентную загрузку
python cli.py verify     # сравнить БД с пакетом и записать отчёт
```

Переменные окружения: обязательный `FALKORDB_URL` (HTTP(S)-адрес FalkorDB Browser),
`FALKORDB_USERNAME` (по умолчанию `default`) и обязательный `FALKORDB_PASSWORD`.
Неявного адреса кластера нет: для локального Browser обычно используется
`http://127.0.0.1:13000`, для стенда укажите его адрес явно.
В базовом загрузчике одноимённая переменная содержит адрес БД вида `falkor://...`,
а здесь нужен HTTP(S)-адрес Browser того же экземпляра FalkorDB.

## Примеры запросов

Месячный график нефти, газа и воды по стволу:

```cypher
MATCH (p:ProductionRecord)-[:FOR_ENTITY]->(w:Wellbore)
WHERE p.granularity = 'month' AND w.npdWellboreId = 7405
RETURN p.periodStart, p.oilVolumeSm3, p.gasVolumeSm3, p.waterVolumeSm3
ORDER BY p.periodStart
```

Суточный профиль для нескольких стволов:

```cypher
MATCH (p:ProductionRecord)-[:FOR_ENTITY]->(w:Wellbore)
WHERE p.granularity = 'day' AND w.npdWellboreId IN [5693, 5769, 7405]
RETURN w.name, p.periodStart, p.oilVolumeSm3, p.waterVolumeSm3
ORDER BY p.periodStart, w.name
```

Данные карты:

```cypher
MATCH (w:Wellbore)
RETURN w.name, w.npdWellboreId, w.latitude, w.longitude
ORDER BY w.name
```

```cypher
MATCH (f:Field {name:'VOLVE'})
RETURN f.geometryGeoJson, f.minLongitude, f.minLatitude,
       f.maxLongitude, f.maxLatitude
```

Месячный профиль месторождения из официального SODIR ряда:

```cypher
MATCH (p:ProductionRecord)-[:FOR_ENTITY]->(f:Field {name:'VOLVE'})
RETURN p.periodStart, p.oilNetVolumeSm3, p.gasNetVolumeSm3, p.waterVolumeSm3
ORDER BY p.periodStart
```

## Ограничения

Координаты wellbore — устьевые точки, а не траектории и не bottom-hole locations.
В набор не включены сейсмика, Eclipse grid и крупные двоичные файлы Volve. Excel
получен из публичного pinned mirror с явным происхождением; география и ряд по
месторождению получены непосредственно из SODIR и закреплены по байтовому SHA-256.
Перед визуализацией следует выбирать одну гранулярность, чтобы не суммировать
суточные и месячные копии одного и того же производственного ряда.
