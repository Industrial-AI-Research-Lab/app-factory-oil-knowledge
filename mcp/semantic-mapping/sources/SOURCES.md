# Исходный пакет Semantic Mapping MCP

`stairs-semantic-mapping/` — неизменённый снимок исходного Python-пакета,
взятый из `C:\Users\user\Downloads\stairs-semantic-mapping`.

Коммит при подготовке снимка:
`3b2ec050b81cf381e512c73ff945d85b2a187bd8`.

Runtime-ядро в `src/stairs_semantic_mapping/` содержит только девять нужных
файлов из этого снимка и два безопасных import-shim-файла. Эти девять файлов
проверяются тестом на побайтовое совпадение со снимком; legacy jobs и их
зависимости в runtime не импортируются. Обновление выполняется заменой
снимка и повторной проверкой, а не ручным редактированием ядра.

## Локальные патчи снимка (отличия от коммита выше)

SQL-паритет `<=>` (дистанция) / `<->` (ранжирование) и поведение
`HierarchicalMapper` при пустых категориях (`where=None`, запрос без
фильтра) — как в апстриме, reversions зафиксированы тестами
`test_validating_preserves_sql_operators`,
`test_query_uses_distance_and_ranking_operators`,
`test_hierarchical_filters_and_validation`.

Отличия, внесённые локально и продублированные 1-в-1 в runtime-срез:

* `infrastructure/embeddings.py` — границы `batch_size`/`timeout`,
  проверка формы/размерности/финитности TEI-ответов (только отказы
  невалидного, валидный путь без изменений).
* `loading/hierarchies/load_hierarchies_data.py`,
  `loading/works/load_works_data.py` — `None`-гарды и валидация
  `chunk_size` (offline ETL индекса, на runtime не влияет).
* `loading/works/works_data_preparation.py` — батчевый embed и
  детерминированный порядок категорий (offline ETL индекса).

При обновлении снимка из апстрима: перенести эти патчи поверх нового
коммита и обновить хеш выше; правильный путь — принять их апстримом
и вернуться к чистому снимку.
