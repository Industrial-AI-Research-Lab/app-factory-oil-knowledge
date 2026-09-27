# Extended Volve Implementation Plan

**Goal:** Populate and verify a separate graph for maps and production charts.
**Architecture:** Async downloads and FalkorDB client; pure data normalization;
deterministic graph package and MERGE loader; independent live comparison.
**Tech Stack:** Python 3.11+ standard library, unittest, ordinary Docker.
**Spec:** SPEC.md. All scope, execution, safety and naming constraints apply.

- [x] Discover source graph, download candidate inputs and verify source schemas.
- [x] Write and execute failing tests for nulls, units, daily/monthly periods,
  duplicate identifiers, NPD joins, SSE errors and graph-name protection.
- [x] Implement `graph_api.py`, `sources.py`, `production.py`, `prepare.py`,
  `load.py`, `verify.py`, `cli.py`; run tests and format with Black/Ruff.
- [x] Download through the finished downloader and generate package plus manifest.
- [x] Load the new graph with an ownership marker; independently verify all records.
- [x] Repeat ingestion and verify idempotence and unchanged source graph.
- [x] Save machine-readable evidence, example queries and Russian README.
