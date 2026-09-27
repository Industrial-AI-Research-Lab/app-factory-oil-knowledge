# Исходный пакет CSV MCP

`stairs-backend/services/backend/bll/services/file.py` — неизменённый снимок
исходного файла из `stairs-backend`.

Коммит при подготовке снимка:
`fea2e0f054116fa411be8204fbd5ab94573ae149`.

Полный backend-файл не импортируется в CSV MCP: он связан с FastAPI,
Pandas, XER/XLSX и application-service слоями backend. Runtime использует
только изолированную CSV-границу, необходимую MCP; исходный файл хранится
рядом для трассируемости и обновляется только заменой снимка.
