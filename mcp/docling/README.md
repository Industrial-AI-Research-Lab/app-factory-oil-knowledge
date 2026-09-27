# AppFactory Lab Docling MCP Wrapper

MCP-обёртка над [Docling MCP](https://github.com/docling-project/docling-mcp), предназначенная для использования в AppFactory.

Wrapper предоставляет единый MCP endpoint для агентов AppFactory, проксирует инструменты Docling и добавляет собственную операцию для экспорта Markdown напрямую в объектное хранилище по presigned S3 PUT URL.

## Архитектура

Система состоит из двух отдельных сервисов:

```text
                    AppFactory Agent
                         |
                         | MCP / Streamable HTTP
                         | http://<host>:8002/mcp
                         v
                +---------------------+
                |  Docling MCP Wrapper |
                |       :8002          |
                +----------+----------+
                           |
                           | MCP / internal Docker network
                           | http://docling-mcp:8000/mcp
                           v
                +---------------------+
                |     Docling MCP     |
                |       :8000         |
                +----------+----------+
                           |
                           | HTTP GET
                           v
                    Presigned S3 URL
                           |
                           v
                          S3
```

В production наружу публикуется только `8002`.

Порт `8000` Docling MCP используется только для внутреннего взаимодействия между контейнерами и не должен публиковаться на host.

## Назначение компонентов

### Docling MCP

Официальный `docling-mcp` отвечает за:

- конвертацию документов;
- создание `DoclingDocument`;
- хранение документов во внутреннем local cache;
- извлечение Markdown;
- работу с anchors;
- поиск текста;
- изменение и экспорт документов.

В текущей архитектуре используется локальный режим конвертации:

```text
DOCLING_MCP_CONVERSION_MODE=local
```

Для локального режима устанавливается:

```text
docling-mcp[local]
```

Официальный `docling-mcp` поддерживает локальные файлы и URL как источники документов.

### Docling MCP Wrapper

Wrapper:

1. подключается к Docling MCP;
2. проксирует его инструменты агенту;
3. добавляет собственные инструменты;
4. получает результаты Docling;
5. при необходимости сохраняет результаты в S3;
6. не передаёт большие Markdown-документы обратно агенту, если операция предназначена только для сохранения результата.

Основной дополнительный инструмент:

```text
export_docling_document_to_markdown_and_upload
```

Он:

1. получает `document_key`;
2. получает Markdown из Docling;
3. извлекает именно поле `markdown` из результата MCP;
4. при необходимости выполняет предварительную обработку Markdown;
5. выполняет HTTP PUT в S3 по presigned URL;
6. возвращает статус операции вместо самого Markdown.

## Работа с файлами

Исходные документы не обязаны находиться на host или внутри контейнера Docling.

Рекомендуемый способ передачи документов в production:

```text
Backend
   |
   | presigned GET URL
   v
AppFactory Agent
   |
   | source=<URL>
   v
Docling MCP
   |
   | HTTP GET
   v
S3 / Object Storage
```

Пример источника:

```text
https://storage.example.com/bucket/document.pdf?X-Amz-Algorithm=...
```

Такой URL передаётся непосредственно в:

```text
convert_document_into_docling_document
```

как параметр:

```json
{
  "source": "https://storage.example.com/...presigned-url..."
}
```

### Важные требования к URL

URL должен возвращать сам файл, а не HTML-страницу.

Например:

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
```

а не:

```http
HTTP/1.1 200 OK
Content-Type: text/html
```

Presigned URL должен быть доступен из контейнера Docling.

Для production предпочтительно использовать presigned S3 URL, а не ссылки на локальный `localhost` или пользовательские файловые серверы.

## Markdown export

Docling возвращает результат экспорта в структурированном виде, содержащем как минимум:

```json
{
  "document_key": "...",
  "markdown": "..."
}
```

Wrapper использует только значение поля:

```text
markdown
```

`document_key` и другие метаданные не записываются в `.md` файл.

Дополнительно Markdown может быть очищен от стандартных Docling image placeholders:

```html
<!-- image -->
```

и от лишних последовательностей пустых строк.

## Версии и совместимость

Проект состоит из двух независимых dependency environments:

### Wrapper

Текущие версии wrapper:

```text
fastmcp>=3.0,<4.0
httpx>=0.27,<1.0
mcp==1.2.0
docling>=2.0.0
uvicorn>=0.30.0
```

Назначение основных зависимостей:

| Пакет | Назначение |
|---|---|
| `fastmcp` | реализация MCP server wrapper |
| `mcp` | Python MCP SDK |
| `httpx` | HTTP-запросы, в том числе загрузка Markdown по presigned PUT URL |
| `uvicorn` | запуск ASGI/MCP HTTP server |
| `docling` | работа с Docling API, если используется непосредственно wrapper'ом |

FastMCP 3.x является отдельной активно поддерживаемой реализацией MCP и рекомендует Python 3.10+.

### Docling MCP

Docling MCP запускается в отдельном контейнере.

Для текущего production-образа используется:

```text
docling-mcp==3.0.0
```

и:

```text
docling-mcp[local]
```

`docling-mcp 3.x` использует MCP Python SDK 2.x и требует:

```text
mcp>=2.0.0,<3.0.0
```

Ветка `docling-mcp 2.x` предназначена для MCP SDK 1.x. Поэтому версии `docling-mcp` и `mcp` нельзя смешивать произвольно.

> **Важно:** `mcp==1.2.0` в wrapper и `mcp>=2.0.0,<3.0.0` в отдельном контейнере Docling — это разные Python environments. Они не должны устанавливаться в один и тот же контейнер.

## Dockerfile Docling

The maintained image definition is `deploy/oil-mcp/docling.Dockerfile`.

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "docling-mcp[local]==3.0.0"

ENV DOCLING_MCP_CONVERSION_MODE=local

EXPOSE 8000

CMD ["docling-mcp-server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
```

`8000` здесь является внутренним портом контейнера.

## Dockerfile Wrapper

The maintained adapter image definition is
`deploy/oil-mcp/docling-adapter.Dockerfile`.

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8002

CMD ["python", "-m", "app.server"]
```

## Конфигурация Wrapper

Wrapper использует переменные окружения:

```text
DOCLING_MCP_URL
HOST
PORT
UPLOAD_TIMEOUT
DOCLING_TIMEOUT
```

Пример конфигурации:

```python
import os


DOCLING_MCP_URL = os.getenv(
    "DOCLING_MCP_URL",
    "http://localhost:8000/mcp",
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8002"))

UPLOAD_TIMEOUT = float(
    os.getenv("UPLOAD_TIMEOUT", "300")
)

DOCLING_TIMEOUT = float(
    os.getenv("DOCLING_TIMEOUT", "300")
)
```

При запуске через Docker Compose URL Docling должен ссылаться на имя Docker Compose service:

```text
http://docling-mcp:8000/mcp
```

## Docker Compose

Поддерживаемая конфигурация находится в едином
`deploy/oil-mcp/docker-compose.yml`. Подготовка runtime env описана в
`deploy/oil-mcp/README.md`.

## Запуск через Docker Compose

Собрать и запустить оба контейнера:

```bash
docker compose -f deploy/oil-mcp/docker-compose.yml up -d --build docling-mcp docling-adapter-mcp
```

## Дополнительная документация

- Docling MCP: https://github.com/docling-project/docling-mcp
- Docling: https://github.com/docling-project/docling
- FastMCP: https://github.com/PrefectHQ/fastmcp
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
