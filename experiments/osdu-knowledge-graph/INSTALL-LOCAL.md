# AppFactory-244 — локальный запуск OSDU knowledge graph demo

Команды ниже — из корня репозитория `AppFactory`, если не сказано иное.

## Предусловия

- Docker Desktop запущен;
- MongoDB доступна на `127.0.0.1:27017`;
- подготовлены `src/venv` и `src/.env` по [local-dev-setup.md](../../docs/local-dev-setup.md);
- в `src/.env` есть `EXTERNAL_MCP_ALLOW_LOCAL_ENDPOINTS=true`;
- backend для этого демо слушаем на `8010`.

### LLM: обычный стенд или локальный LiteLLM

**Вариант A — обычный стенд (Bifrost / корпоративный шлюз)**  
В `src/.env` настроен доступ к LLM так же, как в общей локальной инструкции.
В bundle по умолчанию стоит `iairlab/gpt-oss-120b` у аналитика и критика. Дополнительных переменных для OSDU не требуется.

**Вариант B — локальный LiteLLM (например Duckduck)**  
В `src/.env` дополнительно должны быть:

```env
USE_BIFROST=false
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=<ваш_токен>
LLM_FORCE_CHAT_COMPLETIONS=true
EXTERNAL_MCP_ALLOW_LOCAL_ENDPOINTS=true
```

Секреты в git и в эту инструкцию не копируйте.  
При bootstrap/E2E задайте модель явно:

```powershell
$env:OSDU_DEMO_MODEL="iairlab/gpt-oss-120b"
```

Без `OSDU_DEMO_MODEL` применяется model ID из `config/osdu-demo-bundle.json`.

---

### 1. Зависимость loader

```powershell
src\venv\Scripts\python.exe -m pip install `
  -r experiments\osdu-knowledge-graph\requirements.txt
```

### 2. FalkorDB и MCP

```powershell
docker compose `
  -f docs\graph-db-poc\compose\docker-compose.yml `
  -f experiments\osdu-knowledge-graph\compose\docker-compose.osdu.yml `
  up -d
```

Поднимаются:

| Сервис | Адрес |
|--------|--------|
| FalkorDB | `127.0.0.1:6379` |
| MCP (общий стек) | `127.0.0.1:8080` |
| OSDU read-only MCP | `127.0.0.1:8081` |
| FalkorDB Browser | `127.0.0.1:3000` |

Overlay нельзя запускать отдельно: ему нужен сервис `falkordb` из первого файла.

### 3. Скачать и загрузить Volve-срез

```powershell
Set-Location experiments\osdu-knowledge-graph
..\..\src\venv\Scripts\python.exe -m graph_loader all
Set-Location ..\..
```

Ожидается: 11 Well, 27 Wellbore, 28 WellLog, связи 27 + 28, Q1–Q10 `passed=true`.

Если FalkorDB с паролем (только в текущем процессе):

```powershell
$env:FALKORDB_URL = "falkor://<user>:<secret>@127.0.0.1:6379"
```

### 4. Идемпотентность

```powershell
src\venv\Scripts\python.exe `
  experiments\osdu-knowledge-graph\smoke\verify_idempotency.py
```

Во втором load: `createdNodes: 0`, `createdRelationships: 0`.

### 5. Strict read-only MCP

```powershell
src\venv\Scripts\python.exe `
  experiments\osdu-knowledge-graph\smoke\verify_readonly_mcp.py
```

Проверка: schema, Q1–Q10, отказ `CREATE`, маркер в графе не появляется.

### 6. Backend AppFactory

Отдельный PowerShell:

```powershell
Set-Location src
$env:API_HOST="127.0.0.1"
$env:API_PORT="8010"
$env:PYTHONUTF8="1"
.\venv\Scripts\python.exe main.py
```

Дождитесь готовности API на `http://127.0.0.1:8010`.

### 7. Учётные данные и bootstrap

Ещё один PowerShell **из корня** репозитория `AppFactory`
(не из `experiments\osdu-knowledge-graph`):

```powershell
$env:MONGODB_URI="mongodb://127.0.0.1:27017"
$env:MONGODB_DATABASE="synaps"
$rootCredential = Get-Credential -Message "Local AppFactory root account"
$osduCredential = Get-Credential -Message "Local OSDU demo tenant admin"
$env:AppFactory_ROOT_EMAIL = $rootCredential.UserName
$env:AppFactory_ROOT_PASSWORD = $rootCredential.GetNetworkCredential().Password
$env:OSDU_DEMO_EMAIL = $osduCredential.UserName
$env:OSDU_DEMO_PASSWORD = $osduCredential.GetNetworkCredential().Password
```

Типичные локальные значения (если стенд поднимали по `local-dev-setup.md`):
root `admin@localhost` / `localdev123`; для OSDU-tenant можно задать, например,
`osdu-demo@localhost` / `localdev123` (пароль тот, что введете в `Get-Credential`).

Для LiteLLM (вариант B) добавьте:

```powershell
$env:OSDU_DEMO_MODEL="iairlab/gpt-oss-120b"
```

`OSDU_DEMO_MODEL` действует **только через bootstrap** (пишет model в Mongo).
Одного env перед E2E недостаточно — сначала снова bootstrap.
Bootstrap подставляет промпты из `config/prompts/` в аналитика и критика
(`iairlab/gpt-oss-120b` у обоих, если модель не переопределена).
Workflow: аналитик отвечает → критик проверяет → при REVISE черновик
возвращается аналитику (до 5 правок) → только принятый текст уходит пользователю.
После смены промпта или bundle нужен повторный bootstrap и новый чат.

Затем:

```powershell
src\venv\Scripts\python.exe `
  experiments\osdu-knowledge-graph\demo\bootstrap_osdu_demo.py
```

Bootstrap создает/переиспользует tenant `osdu_demo`, импортирует MCP
`osdu-graph-ro`, применяет bundle (два агента + workflow аналитик → критик →
шлюз) и проверяет, что у аналитика только `query_graph_readonly` и `get_node_schema`.

### 8. UI (ручная проверка)

```powershell
Set-Location src\ui
$env:VITE_API_TARGET="http://127.0.0.1:8010"
npm run dev
```

Без `VITE_API_TARGET` Vite по умолчанию проксирует `/api` на `:8000` — логин
в `osdu_demo` на сайте не сработает при backend на `:8010`.

1. Откройте <http://127.0.0.1:5173/login>
2. Войдите как `OSDU_DEMO_EMAIL` / `OSDU_DEMO_PASSWORD` (не root)
3. Создайте проект с workflow **`osdu_graph_demo`**
4. Задайте один вопрос из раздела
   [«Контрольные вопросы и ожидаемые ответы»](README.md#контрольные-вопросы-и-ожидаемые-ответы)
   в `README.md` (Q1–Q10 или FREE)

Ответ агента — обычный текст (не фиксированный JSON). Эталоны сверяются
гибко: важны факты, а не дословная формулировка.

### 9. Автоматический E2E

В **том же** терминале, где заданы env из шага 7
(`MONGODB_URI`, `MONGODB_DATABASE`, `OSDU_DEMO_EMAIL`, `OSDU_DEMO_PASSWORD`;
при LiteLLM — после bootstrap с `OSDU_DEMO_MODEL`):

```powershell
# cwd должен быть корень AppFactory
src\venv\Scripts\python.exe `
  experiments\osdu-knowledge-graph\demo\run_osdu_demo.py
```

Без `MONGODB_URI` / `MONGODB_DATABASE` runner не сможет прочитать ответ агента
из Mongo и упадёт сразу после первого completed-проекта.

Runner прогоняет те же сценарии, что перечислены в
[README.md](README.md#контрольные-вопросы-и-ожидаемые-ответы)
(Q1–Q10 + FREE про drilling cost).  
Проверяются status, успешный readonly MCP call и содержимое ответа; затем
отдельный MCP postcondition. Имена и метки сверяются как целые токены
(`SR` ≠ `SR2`, `Well` ≠ `Wellbore`); FREE принимает явный отказ, что
стоимости в графе нет (русский или английский: «не хранится»,
`does not store`, `no cost property`), а не цену и не «нет, стоимость 1000».
Сценарий повторяется до 3 попыток, если модель вернула сырой `{graphName, query}`, неполный набор фактов или FREE без явного «данных нет».

Ожидаемый итог: у всех сценариев `"verified": true`.

### 10. Unit-тесты

```powershell
Set-Location experiments\osdu-knowledge-graph
..\..\src\venv\Scripts\python.exe -m unittest discover -s tests -v
Set-Location ..\..
```

После unit снова вернитесь в корень `AppFactory` (`Set-Location ..\..` отдельной
командой). Не склеивайте `Set-Location` и следующий `python` в одну строку —
пути `src\venv\...` рассчитаны от корня репозитория.

### 11. Browser

Откройте <http://127.0.0.1:3000>, host `localhost`, port `6379`, граф
`osdu-volve`:

```cypher
MATCH (log:WellLog)-[:BELONGS_TO_WELLBORE]->
      (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well)
RETURN log, wb, w
LIMIT 25
```

## Остановка

Backend и frontend: `Ctrl+C`.

```powershell
docker compose `
  -f docs\graph-db-poc\compose\docker-compose.yml `
  -f experiments\osdu-knowledge-graph\compose\docker-compose.osdu.yml `
  down
```

Без `-v`, если нужно сохранить граф `osdu-volve`.
