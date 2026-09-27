# Research MCP MVP fixtures

Этот каталог хранит версионируемые входы и контрольные наборы для
[активного плана MVP](../../docs/research-mcp-mvp-plan-2026-09-16.md).
Fixture bundle состоит только из `data/`, `evaluation/` и `negative/`.
Его точный состав, размеры и SHA256 перечислены в `package-lock.json`, а
результат последней полной сверки — в `directory-integrity.json`.

## Состав и назначение

| Путь | Роль | Передавать runtime-агенту |
|---|---|---|
| `data/news/` | Offline replay для provider stub и детерминированных web-проверок | Нет для live-поиска |
| `evaluation/news-*` | Ожидаемые признаки offline web-результата | Нет |
| `data/knowledge/` | Входной корпус Demo 3 | Да, после approval manifest |
| `evaluation/knowledge-*` | Контрольные вопросы и факты Demo 3 | Нет |
| `data/terminology/` | Канонические словарь и таксономия v2, а также prompt и руководство | Только нужные файлы |
| `evaluation/ontology-v2/` | Редакторская разметка, использованная при подготовке v2 | Нет |
| `negative/` | Намеренно повреждённые входы для отрицательных проверок | Только в соответствующей проверке |

Архив из 26 публикаций не заменяет live Tavily в Demo 1 и не является web
index. Он нужен только для воспроизводимого replay, dedup, evidence и
классификационных проверок. Knowledge corpus содержит 116 фрагментов из трёх
документов и остаётся входом Demo 3. Эти два набора не смешиваются.

`data/knowledge-v2/manifest.json` описывает корпус `ipr4-pav-40`: 40 книг из
архива ПАВ целиком, после удаления дублей — по названию и по совпадению текста
чанков — с разрезанием чанков длиннее 6000 символов. Отброшенные дубликаты
перечислены в `excluded_duplicates` манифеста. Сами JSONL-файлы (около 61 МиБ)
не коммитятся: они собираются командой
`python corpus_v2.py build --source <graphrag_corpus.jsonl>`
в `build/knowledge-v2/` и проверяются по manifest командой `python corpus_v2.py verify`.
Это вход workflow `knowledge_collection_v2`; корпус `data/knowledge` остаётся входом
старого workflow и не меняется.

Для показа старого workflow `knowledge_collection_mcp` на большем корпусе к
первому сообщению прикладываются десять файлов из `build/knowledge-v2/` с
префиксами 02, 05, 06, 09, 11, 13, 17, 26, 32, 40 — книги, где больше всего
фрагментов о межфазном натяжении с единицами и условиями. Composer принимает не
больше 10 файлов на сообщение. Эти книги дают 2922 фрагмента и 11,8 МиБ, индекс
Domain MCP — 34,4 МиБ при лимите `MAX_DOWNLOAD_BYTES` 50 МиБ. Запрос тот же, что
для трёх документов, только без их числа:

> Построй по приложенным документам проверяемую онтологию для коллекции знаний.
> Нужны рецептуры, компоненты, концентрации, температура, минерализация и
> межфазное натяжение. Я хочу сравнивать результаты только при сопоставимых
> условиях и видеть пробелы. Покрой вопросы: какие рецептуры и компоненты
> описаны; какие значения межфазного натяжения можно сравнивать; при каких
> температуре, минерализации, концентрации и методе получено наблюдение; где
> условия отсутствуют; какой исходный фрагмент подтверждает каждую часть схемы.

Канонический JSON-набор терминологии v2 —
`data/terminology/terms.json` и `data/terminology/news-taxonomy.json`:
77 понятий и 16 категорий. Он используется для query decomposition и
классификации. `terminology-prompt.md` сопровождает JSON, а
`evaluation/ontology-v2/` фиксирует 88 редакторских тем и source review, но не
входит в канонический JSON-набор.

## Integrity

Пути `files[].path` в manifests разрешаются относительно каталога manifest.
SHA256 считается по фактическим байтам файла. Полная проверка bundle:

```text
python -m unittest discover -s tests -p "test_fixture_bundle.py" -v
```

Проверка требует точного совпадения: каждый файл в трёх fixture-каталогах
должен находиться в lock, а runtime-файл не может попасть в lock. При изменении
fixture сначала обновляется его локальный manifest, затем `package-lock.json`
и `directory-integrity.json`.

## CFG01/NEWS01: MCP-роли и workflows

Общий source-config шести ролей находится в
[`config/mcp-agents.json`](config/mcp-agents.json), knowledge workflow — в
[`config/knowledge-collection-workflow.json`](config/knowledge-collection-workflow.json),
news workflow — в
[`config/news-research-workflow.json`](config/news-research-workflow.json), а готовый
import payload — в
[`config/mcp-config-bundle.json`](config/mcp-config-bundle.json). Роли используют
минимальные built-in и MCP allowlists; report_builder дополнительно получает attachment_presign_get для проверки артефактов; прикладные
`bash`, `read`, `create`, `attachment_fetch` и Container-Use им не выдаются.
Общий `report_builder` выбирает family только по входу: knowledge использует
шаблон `1.2.0`, news — `1.0.0`. Одновременное наличие обоих typed payload
считается ошибкой, поэтому news-конфигурация не меняет knowledge-контракт.

Контракты checksum-проверки, canonical exports и follow-up описаны в
[`research-mcp-artifact-integrity.md`](../../docs/research-mcp-artifact-integrity.md).

Текущий bundle привязан к wire-ID, обнаруженным на внешнем dev-стенде:
`d2_*` для Domain Research и `r_render_report` для Reporting. Перед импортом в
другой tenant или stand оператор обязан получить фактический каталог tools,
заменить только MCP bindings, пересобрать bundle и повторить автоматические
проверки, dry-run и read-back. Импорт не делает workflow default. Новые роли и
workflow нельзя выбирать для демо, пока read-back не подтвердит точное
совпадение обоих allowlist-полей и графа.

Первый dev apply выявил и воспроизвёл platform-дефект: встроенные attachment
tools с подчёркиваниями нормализовались как Path-A MCP tools. Regression и
минимальное исправление находятся в основном backend-коде. После его доставки
на dev применён точечный repair пяти ролей: 5 success, 0 errors. Повторный
read-back всех шести ролей и workflow совпал с source-config, повторный repair
dry-run дал нулевой diff для commit `eda2edd3`. Последующее review исправило в
commit `cc4f2f55` ещё два сквозных контракта: gate теперь использует сохранённый
`writes`-результат, а `analysis_ready` проверяет typed report payload и
строковые версии. После deploy на dev выполнен targeted apply: 2 success,
0 errors; read-back шести ролей и workflow совпал с source-config, повторный
targeted dry-run дал нулевой diff. CFG01 готова для G01; knowledge project
относится к G01 → H05 и в CFG01 не запускался. Точный статус, ID, SHA-256,
rollback bundle и повторяемый prod runbook сохранены в
[`docs/research-mcp-cfg01-evidence-2026-09-20.md`](../../docs/research-mcp-cfg01-evidence-2026-09-20.md).

Краткий inventory для будущего CLEAN01, без удаления в CFG01:

- Container-Use bundle: `config/agent.json`, `config/workflow.json`;
- прежний news bundle: `config/news-agents.json`, `config/news-workflow.json`;
- прикладной CLI: `command_io.py`, `commands.py`, `news_*.py`, `COMMANDS.md`,
  `NEWS-COMMANDS.md`;
- старый reporting bridge: `reporting/` и его `reporting/config/`;
- legacy tool aliases: `bash`, `read`, `create`, `attachment_fetch`,
  `list_files`, `read_file`, `create_file`, `edit_file`, `delete_file`,
  `find_replace_in_file`, `run_command`.

Это только кандидаты на отдельную проверку и точечное удаление после C02.
Продуктовые MCP-сервисы, автоматические тесты, audit trail и fixtures в этот
список не входят.

NEWS01 добавляет только web-часть bundle и не делает workflow default. На dev
подтверждены 12 Domain tools и `render_report`; pre-apply dry-run также
воспроизвёл platform-дефект старого backend, запрещавшего межшаговые
`value_relation` в обычных validator nodes. Минимальное исправление и regression
находятся в основном backend-коде. Применять NEWS01 bundle можно только после
доставки этого исправления и успешного повторного dry-run. Фактический статус,
порядок dev-проверки и перенос на production описаны в
[`docs/research-mcp-news01-dev-prod-runbook-2026-09-21.md`](../../docs/research-mcp-news01-dev-prod-runbook-2026-09-21.md).

## Ограничения проверки

Материалы подготовлены Codex по доступным текстам. Проверка человеком —
доменным экспертом не проводилась, независимого quality benchmark нет. Те же
исходники использовались для редакторского улучшения терминологии v2, поэтому
`evaluation/ontology-v2/` нельзя использовать как независимое доказательство
роста качества. Непрочитанная часть исходного архива из 292 публикаций не
входит в заявленное покрытие.

Исходные URL фиксируют происхождение, но их текущая доступность не
гарантируется. Материалы предназначены для разрешённого внутреннего контура;
bundle не публикует архив во внешнем доступе.

## Legacy scope

`COMMANDS.md`, Python CLI, `config/`, `tests/`, `templates/` и `reporting/`
остаются историческим runtime до подтверждения parity новых MCP-сервисов. Они
намеренно не входят в fixture lock. Прежний общий lock версии `1.3.0` и прежний
directory-integrity описывали смешанный снимок fixtures/runtime и считаются
legacy metadata; текущие `package-lock.json` и `directory-integrity.json`
фиксируют только три fixture-каталога, перечисленные выше.
