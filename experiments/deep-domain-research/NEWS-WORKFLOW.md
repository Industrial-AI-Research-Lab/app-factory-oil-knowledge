# N01: запуск исследования новостей в AppFactory

**Статус: runtime реализован, локальные контракты проверены.**
Локальный стенд с настоящим backend/MongoDB и мокированным Bifrost проверяет
обычное и typed согласование, уточнение через Chat, сохранение того же gate ID
и передачу фактически утверждённой версии следующему агенту. Полный bundle
проходит native validate, dry-run и apply в изолированном локальном tenant.
Полный пользовательский N01/N02 сценарий с реальным Bifrost и Container-Use
на production ещё не принят; обращения к недоступному production остановлены.
Workflow `ipr4_news_research` предназначен для OIL и режима `human`.
Конфигурация: [news-workflow.json](config/news-workflow.json), шесть ролей
GenericAgent с полными system prompts: [news-agents.json](config/news-agents.json).
Команды и точные форматы входов: [NEWS-COMMANDS.md](NEWS-COMMANDS.md).
Новые классы агентов, сервисы и форма запуска не требуются.

## Импорт и доставка

Перед запуском проверьте фактически развёрнутую версию в MongoDB `synaps.system_info`.
Сверяйте доступные tools и GenericAgent с этой версией. Эти файлы сами по себе
не являются свидетельством импорта, выполнения или приёмки на стенде.

Для проверки структуры отправьте `POST /api/configurations/workflows/validate`:

```json
{
  "nodes": ["содержимое nodes из news-workflow.json"],
  "edges": ["содержимое edges из news-workflow.json"],
  "execution_mode": "dynamic"
}
```

Строки в примере заменяются фактическими массивами объектов, не передаются буквально.
Готовый request: [workflow-validate-request.json](../../tasks/ipr4-demo/n01-n02/workflow-validate-request.json).
Для штатного `POST /api/admin/config-bundle/dry-run`, затем `/apply` используйте:

```json
{
  "target_tenant_id": "фактический tenant_id OIL из профиля",
  "bundle": {
    "version": 1,
    "items": {
      "agents": ["объекты из news-agents.json"],
      "workflows": ["объект news-workflow.json"]
    }
  }
}
```

Объекты используют стабильные `id`/`name`, пригодные для bundle; отдельный
`display_name` задаёт подпись. Импорт не делает workflow дефолтным и не меняет
другие роли. Проверяйте результат каждого элемента bundle, а не только HTTP 200.
Готовый request для подтверждённого OIL tenant `cooilman`:
[news-bundle.json](../../tasks/ipr4-demo/n01-n02/news-bundle.json).
Точные операторские вызовы и получение ID:
[operator-controls.md](../../tasks/ipr4-demo/n01-n02/operator-controls.md).

Пакет `news-delivery-*.py` доставляется через обычное вложение проекта.
Разрешённый операторский пакет распаковывает код в `/workdir/ipr4-news`,
новости в `inputs/news`, B03 в `inputs/terminology`. В `evaluation/` и `negative/`
нет входов исследующего агента; они не передаются вместе с рабочим пакетом.
Агент получает вложение через `attachment_list`/`attachment_fetch` и запускает
его через `bash` текущего Container-Use. Исследование исполняется в этом же
контейнере, изменяемые файлы — только в `results/<project_id>/<run_id>/`.

WorkflowEngine добавляет фактические `project_id`/`run_id` в `task.context`
для direct и auction фаз. Модель получает их в TASK CONTEXT до первой
постановки; технический ask_human и ручное копирование ID не нужны.
После gate защищённое поле `workflow_approval` связывает реальную запись
решения с точным показанным результатом. Контракт платформы:
[workflow-approval-context.md](../../docs/contracts/workflow-approval-context.md).

Модель ролей по умолчанию — `openai/gpt-5`; её параметры reasoning валидны
в canonical registry AppFactory. Доступность этой модели у production-провайдера
и смысловое качество полного исследования ещё требуют живой приёмки.
## Состояния и переходы

| Состояние | Действие | Следующий переход |
|---|---|---|
| Требования | Проверить оба manifest, сформировать и сохранить версию `brief` | Проверка формы → карточка |
| Ожидание человека | Показать `requirements`, полный brief и reference | Approve → проверка текущей версии |
| Уточнение через Chat | Повторить только требования, сохранить новую версию | Та же pending карточка |
| Отклонение карточки | Передать причину в требования | Повторное согласование |
| Терминология | Снова проверить доступность корпуса, прочитать B03 | Сохранить поисковые расширения |
| Исследование | Выполнить candidates, оценить исходные тексты | Черновик критика |
| Критика | Проверить решения, цитаты и группировку | Одно исправление |
| Исправление | Обновить решения/замечания, выполнить finalize | Проверка успешного результата |
| Итог | Объяснить вычисленный JSON в Chat | End |

У gate `approve_news_brief` есть только переходы `approved` и `rejected`.
Нет безусловного перехода, ведущего к поиску. Терминология, candidates и finalize
недоступны по графу, пока native gate остаётся pending. Режим `auto` платформы
обходит человеческие согласования и не подходит для приёмки N01.

`writes=["requirements"]` и legacy `output_save_key="requirements"`
сохраняют весь итог требований. У gate отсутствует свой `interaction_schema`:
`WorkflowEngine.build_approval_data` создаёт штатный `agent_result_review`
с `agent_result.output`. После восстановления карточка собирается из объявленного
`writes`, поэтому не зависит от исчезнувшего ответа агента в памяти.

Уточнение через Chat обрабатывает существующий intent classifier. Native
`_refine_approval_core` сохраняет сообщение пользователя, повторяет ближайшую
фазу требований, обновляет данные той же pending карточки и испускает
`approval_updated`. Новый проект/запуск для уточнения не создаётся. Отдельный
router со списком слов подтверждения не нужен. Нажатие Reject использует
обратное ребро gate → requirements и штатный жизненный цикл нового согласования.

После gate есть повторная проверка `requirements.status == "ok"`, потому что
native refine повторяет фазу напрямую, не проходя её исходный validator.
Ошибка новой постановки не должна запустить поиск после нажатия Approve.
Validators этапов проверяют JSON-форму и `status`, не оценивают смысл ответов.
Отсутствие `rejected` ребра у этих validators означает явную ошибку workflow.

## Контракты SharedContext и файлов

| Ключ | Содержимое |
|---|---|
| `requirements` | `{status:"ok",brief:<data.brief>,reference:<data.reference>}` после prepare_brief |
| `news_terms` | `{status:"ok",terminology_result:<B03>,approval:{approval_id,gate_node_id,brief_sha256}}` |
| `news_draft` | `{status:"ok",candidates_path,draft_path}` |
| `news_critique` | `{status:"ok",review_path,findings:[...]}` |
| `news_result` | `{status:"ok",result:<data ответа finalize>}` |
| `news_summary` | Итоговый русский текст Chat |

SharedContext добавляет к требованиям `user_prompt` и `updated_at`; checksum
относится к сохранённому CLI документу `brief`, а не к расширенной оболочке
SharedContext. Новая версия получает новый файл. Старая не перезаписывается;
история требований различима по версиям, checksum, файлам и сообщениям.

Терминолог читает защищённый `workflow_approval`, который движок сохраняет
из фактической approved записи MessageStore до продолжения workflow.
Он сверяет project/run из TASK CONTEXT, gate `approve_news_brief`, approval ID
и `data.agent_result.output.reference` с текущими requirements.reference.
Из совпавшего approved reference формируется `brief_sha256` для CLI receipt.
Обычное штатное Approve и typed ответ работают одинаково; ручной операторский
receipt и зависимость от поздней записи hitl_feedback_history устранены.

Native gate разрешает переход; receipt связывает результат с просмотренной
версией и не является криптографическим доказательством. При отсутствии или
несовпадении actual approved записи движок завершает запуск структурированной
ошибкой до следующей стадии. Если сама модель видит несовпавшие references,
она возвращает `APPROVAL_RECEIPT_UNAVAILABLE`. Refine оставляет прежний pending
approval ID, но обновляет snapshot и previous_output следующей стадии.
`candidates_path` хранит полный stdout JSON команды, включая `data.reference`.
`draft_path` хранит raw `analysis` с `publications` и `events`.
`review_path` хранит raw `critique` с `notes` и `correction_cycle`.
Корректор читает эти файлы, оставляет исходный analysis, передаёт в finalize
`candidates_ref`, `initial_analysis`, `analysis`, `critique` и общие поля.
Схемы не дублируются в отдельной системе: точные поля описаны в NEWS-COMMANDS.md.

Один отдельный узел исправления всегда проходит после критика. Если замечаний
нет, решения сохраняются и `correction_cycle=0`. При исправлении он равен 1.
Обратного ребра к критику/исследователю нет. Неразрешённые замечания остаются
в результате. Техническая коррекция формата CLI-запроса не разрешает второй
смысловой пересмотр или изменение доказательств ради успешного exit code.

Finalization вычисляет статистику. Корректор читает сохранённый файл по
`/workdir/ipr4-news/results/<news_result.result.reference.path>`, затем передаёт
тот же текст в штатный `create` для регистрации JSON в Artifacts. Доступность
артефакта и его checksum проверяются на стенде отдельно; stdout команды и ответ
Chat сами по себе не доказывают выдачу файла. HTML/CSV/PDF относятся к N04/B05.

## Сценарий проверки

1. В Projects под OIL выберите workflow «ИПР-4: исследование новостей» и `human`,
   приложите разрешённый пакет и задайте широкий стартовый запрос из workflow.
2. При необходимости передайте фактические project/run ID. Дождитесь версии 1
   в штатной карточке. Для основного показа используется полный selected_period
   manifest: 2025-03-12—2026-03-02. Не запускайте отдельное исследование вручную.
3. В Chat отправьте реальное уточнение: «Оставь управление заводнением и подбор
   нефтепромысловой химии; различай заявления и описанные испытания».
   Убедитесь, что новая версия содержит оба направления, сохраняет период,
   исключения и происхождение; pending approval ID при refine остаётся тем же.
4. Повторно откройте страницу: должны сохраниться проект, версия и карточка.
   До согласования не должно быть вызовов candidates/finalize.
5. Подтвердите постановку через typed карточку. Сверьте её reference с
   `brief_ref` фактического вызова candidates и проверьте исходные тексты/решения.
6. Проверьте отдельный вывод критика, не более одной правки и оставшиеся
   ограничения, затем пересчитайте публикации и события по news-result.json.
7. Отдельным прогоном проверьте 2025 год, период вне архива и ошибки доступа.
   Исключённая по дате публикация не заменяет проверку её смысловой нерелевантности
   в основном полном периоде. Эталоны оценивания остаются у проверяющего.

Свидетельства живого прогона: version из system_info, tenant/project/run ID,
ответы validate/dry-run/apply, две версии brief, сообщение уточнения,
approval ID и фактическую approved запись, реальные вызовы контейнера, candidates,
initial/final analysis, critique и news-result.json с проверкой Artifacts.
Настоящее описание фиксирует конфигурационный контракт; пока эти свидетельства
не получены, оно не подтверждает прохождение критериев N01/N02 на стенде.
