# B04: команды контейнера ИПР-4

Основная рабочая директория — `experiments/deep-domain-research/`.
Общий состав пакета: [README.md](README.md).

Небольшой пакет Python 3.11+ (только стандартная библиотека), GenericAgent и
недефолтный проверочный workflow. LLM вызывается платформой через её gateway.
Команды исполняются только инструментом `bash` существующего Container-Use.
Доменные обработчики добавляются в N02/G02/G03/G04, рендер и выдача бинарных
файлов — в B05. Отдельных серверов и классов агентов здесь нет.

## Файлы и доставка

- `commands.py`, `command_io.py`, `corpus.py`: CLI, запись результатов, чтение корпуса.
- `tests/`: поведенческие проверки без ожидаемых ответов доменных кейсов.
- `config/agent.json`, `config/workflow.json`: конфигурации штатных механизмов.
- `templates/`: место для реализации B05; готового генератора здесь пока нет.
- Корпуса B02 находятся в `data/` этого же пакета.

Оператор собирает вложение утилитой
`tasks/ipr4-demo/b04/operate.py package --output b04-delivery-green.py`.
Это упаковка байтов на рабочем компьютере, а не исполнение прикладных команд.
Утилита включает код, конфигурации, тесты и **только** `data/` B02; каталоги
`evaluation/` и `negative/` не попадают в архив или контекст исследующего агента.
Имена `tmp/` внутри B02 — история происхождения, а не runtime-зависимости.

Приложите полученный `.py` при создании проекта с B04 workflow. Агент вызывает
`attachment_list` → `attachment_fetch` → `bash`: `python3 <скачанный путь>`.
Вложение проверяет SHA256 архива и распаковывает его в `/workdir/b04`.
Этот каталог — выбранное место доставки, CLI не требует такого абсолютного пути.
Повторная доставка не заменяет отличающиеся исходные данные; новая версия корпуса
должна получить новый каталог или проект. Изменяемые файлы лежат только в `results/`.

Перед импортом передайте `config/` в штатный bundle (`items.agents`,
`items.workflows`, `version=1`) и укажите tenant из профиля текущего пользователя:
`POST /api/admin/config-bundle/dry-run`, затем `/apply` при отсутствии ошибок.
Отдельно проверьте nodes/edges через `POST /api/configurations/workflows/validate`.
OIL имеет свой tenant; ROOT для этого пакета не требуется. Workflow служит
проверке команд; согласования доменных требований реализуются N01/G01/G02.

Для повторного задания используйте `POST /api/projects/{id}/resume` с
`{"prompt": "точное задание и команда с project/run ID"}`. Это вновь запускает
назначенный workflow. Обычное сообщение после `completed` может попасть к другому
агенту через аукцион (в проверке QA оказался без bash). Не полагайтесь на этот
маршрут для повторного запуска CLI. Передавайте файл обновления вместе с запросом
создания проекта; для существующего проекта его можно приложить к сообщению
«Только сохранить вложение; команд не выполнять», затем явно вызвать resume.

## Вход и выход

```sh
python3 commands.py --inputs inputs --results results request.json
python3 -m unittest discover -s tests -v
```

`request.json` — UTF-8 JSON object. Обязательные поля каждого запроса:

```json
{
  "operation": "read_fragment",
  "project_id": "реальный-ID-проекта",
  "run_id": "реальный-ID-запуска",
  "corpus_id": "ipr4-pav-small",
  "corpus_version": "1.0.0",
  "manifest": "knowledge/manifest.json",
  "source_id": "94b2e826-08d1-42b2-a95c-c09d7d3b4ecf"
}
```

Идентификаторы проекта/запуска передаются явно из backend; CLI не определяет их
по тексту разговора. `manifest` относительно `--inputs`; `files[].path`
относительно manifest. Все файлы manifest проверяются по SHA256 до операции.
Корпус и версия должны совпасть с запросом. Пути за пределами входов запрещены.

| operation | Дополнительный вход | data при успехе |
|---|---|---|
| `read_manifest` | — | `manifest`, `manifest_sha256`, `file_count` |
| `read_fragment` | `source_id` | исходная `record` целиком, `source_path` |
| `read_term` | `terms_path`, `term_id` | `record`, `source_path` |
| `save_text` | `name`, непустой `text`; опционально `schema_version`, `collection_version` | `path`, `metadata_path`, `sha256`, `bytes`, `encoding`, `newlines` |

Фрагменты читаются из B02 `fact_source` JSONL по `es_id` или из индивидуальных
публикаций manifest по `publication_id`. Вспомогательные материалы не становятся
источниками фактов. Терминологический JSON должен быть явно перечислен в manifest
с `role=terminology` и содержать `terms: [{id, ...}]`; `terms_path` относительно
manifest. Справочник B03: `inputs/terminology/terms.json`, отдельный manifest
`terminology/manifest.json`, corpus_id `ipr4-terminology`, версия `2.0.0`.
Для `read_term` укажите `terms_path: "terms.json"` и ID из справочника.
Категории и prompt читаются штатным `read` после проверки их manifest командой
`read_manifest`; дополнительный классификатор по ключевым словам не вводится.
Целостность B03 и чтение всех терминов через CLI проверяются в контейнере:

```sh
python3 <delivery-root>/tests/check_terminology.py --project-id <id> --run-id <id>
python3 -m unittest discover -s <delivery-root>/tests -v
```

`<delivery-root>` — корень фактически доставленного пакета, содержащий `inputs/`.
Для ревью v2 используется отдельный `/workdir/b03-review-20260914`; старый B03
сохранён. Проверка сверяет ID, типы связей, миграцию, точные цитаты, происхождение
ключевых фраз и checksum, но не измеряет смысловое качество. Основания правок v2:
`tasks/ipr4-demo/b03-review-2026-09-14/review-report.md`.
Исторический пример v1: `tasks/ipr4-demo/b03/terminology-check.md`.

JSON stdout: `{status: "ok", context: {...}, data: {...}}` и exit 0 либо
`{status: "error", context: {...}, error: {code, message, ...}}` и exit 1.
Ошибки включают UNKNOWN_ID, MISSING_FILE, INVALID_JSON, INVALID_INPUT,
INVALID_PATH, VERSION_MISMATCH, CHECKSUM_MISMATCH, DUPLICATE_ID, RESULT_CONFLICT.
Контекст включает операцию, project/run, corpus/version и переданные версии схемы
и коллекции. stderr содержит `[B04]` начало, версию, число файлов, итог/ошибку.
Пустые значения и неоднозначные ID не выдаются за успех.

## Сохранение и повтор

`save_text` создаёт `results/<project_id>/<run_id>/<name>.txt` и `.json` с
исходным запросом и checksum. UTF-8 без BOM, CRLF/CR заменяются LF. SHA256
вычисляется повторным чтением сохранённых байтов. Если передаётся версия схемы,
нужна также версия коллекции (и наоборот). Новое содержание под прежним именем
отклоняется: для правки используйте новое имя результата или новый run.

Повторите CLI с `request` из сохранённого metadata JSON. Неизменный запрос
возвращает тот же результат; отличающийся запрос не перезаписывает его.
Каталоги разных project/run разделены. После `save_text` агент читает TXT и его
metadata через `read`, затем вызывает штатный `create` по тем же путям с тем же
содержимым. Это регистрирует текст в Artifacts. Один `bash`/`read` после resume
в проверенной версии не обеспечил snapshot. Реальную доступность и SHA256
надо сверить через `GET /api/projects/{id}` → `artifacts`. Текст в Chat не
заменяет артефакт. Путь и SHA256 stdout сами по себе не доказывают публикацию.

Свидетельства живой проверки: `tasks/ipr4-demo/b04/command-check.md`.
