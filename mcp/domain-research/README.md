# Domain Research MCP

Stateless FastMCP service for the research tools described in the
[research MCP design](../../docs/superpowers/specs/2026-09-16-research-mcp-services-design.md).
The service exposes Streamable HTTP at `/mcp` on port `8003`.

## Tools

`search_web` performs Tavily `general` or `news` searches and returns bounded,
normalized results. It accepts exact start/end dates, include/exclude domain
filters, exact-match mode, `basic` or `advanced` depth, and 1–20 results.
Provider and input failures use a stable structured error response with a
local request ID. Successful responses keep the provider's identifier in the
separate `provider_request_id` field.

`extract_web_pages` uploads a canonical source bundle and returns its SHA-256
plus a service-signed receipt. `read_web_fragments` requires both values,
rejects checksums not attested by extraction, and rejects changed bundle bytes
before parsing them.

`finalize_news_result` accepts the approved brief, final publication decisions,
semantic event groups, and reviewer findings. It requires the same bundle
SHA-256 and signed receipt as `read_web_fragments`, verifies every source and
excerpt against that snapshot, binds its query to the approved brief, rejects
inconsistent exact-repost grouping, computes deterministic totals, and uploads
canonical `news-result.json` bytes.
The workflow owns draft comparison and the single correction cycle; the tool
validates only the final reviewed state and does not import the legacy CLI.

The document tools build and search immutable SQLite indexes, validate ontology
and fact artifacts against approved checksums, and build, inspect, and query
read-only research collections.
`read_ontology` accepts a signed GET URL and the approved SHA-256, rejects
changed bytes or an invalid ontology, and returns the validated schema without
the signed URL. It has no write side effect.

`validate_calculator_spec(collection_url, expected_collection_sha256,
ontology_url, expected_ontology_sha256, spec, upload_url)` parses and
type-checks a calculator spec (formula grammar, input/output shape), verifies
the spec's own `ontology_sha256`/`collection_sha256` pins against the
downloaded artifacts, checks every collection-bound input's observation type
and unit against the ontology, materializes matching observation rows from
the collection, evaluates the formula over them, and uploads one canonical
JSON bundle (`{schema_version, kind: "calculator", calculator_id, spec,
dataset, ontology_sha256, collection_sha256}`) to `upload_url`. On success it
returns `{"status": "ok", schema_version, calculator_id, ontology_sha256,
collection_sha256, row_counts, sample_count, sha256, size_bytes}`, where
`row_counts` maps each collection input's id to its materialized row count.
Rows per input are capped at `min(MAX_QUERY_ROWS, 200)`; each row's `quote` is
the first 400 characters of the citing fragment's text. A row whose matching
observation carries a different unit than the input (or a non-numeric value,
or a missing required condition) is skipped and counted, never substituted or
converted; an input left with no usable row fails the call. On failure the
tool returns `{"status": "error", "error": {"code", "message"}}` with one of:

| Code | Fires when |
|---|---|
| `CALCULATOR_SPEC_INVALID` | `spec` is not an object, or fails schema validation (bad shape, duplicate/overlapping ids, out-of-range list lengths) |
| `CALCULATOR_FORMULA_INVALID` | the formula fails to parse (bad token, too long, too deep, too many tokens, wrong arity) or its identifiers do not exactly match the spec's input ids |
| `CALCULATOR_FORMULA_DOMAIN` | evaluating the formula on a real sample hits division by zero, a non-positive `log`, a negative `sqrt`, `interp` outside its observed range, or a non-finite result |
| `CALCULATOR_PIN_MISMATCH` | the spec's own `ontology_sha256`/`collection_sha256` do not equal the downloaded artifacts' checksums |
| `CALCULATOR_BINDING_UNKNOWN` | a collection input names an observation type the ontology does not declare |
| `CALCULATOR_BINDING_TYPE_INVALID` | the bound observation type is not numeric, or requires a unit the input does not name |
| `CALCULATOR_BINDING_SUBJECT_INVALID` | the input's `subject_type` disagrees with the bound type's declared subject type |
| `CALCULATOR_BINDING_CONDITION_UNKNOWN` | a `condition_filters` key is not a condition the bound observation type declares |
| `CALCULATOR_BINDING_EMPTY` | after skipping non-numeric, wrong-unit and missing-condition rows, an input has no usable row |
| `CALCULATOR_THRESHOLD_UNKNOWN` | a threshold's `source` (other than `"assumption"`) is not an existing observation id — of any type |

The row query and each input's type-count share the general
`SQLITE_OPERATION_BUDGET` with `query_collection`. Classifying *why* rows
were skipped (non-numeric, wrong unit, missing condition) needs its own scan
of up to 20 000 rows of the type, so that scan runs under the separate
`CALCULATOR_SCAN_BUDGET` (default `1000000`) instead — a sparse observation
type does not exhaust the row query's shared budget just to explain its own
skip counts. Either scan hitting its budget or the row cap logs a warning and
reports partial skip counts rather than failing the call.

Under the default `SQLITE_OPERATION_BUDGET` (`100000`), the type-count query
is the first of the two to break as an observation type grows: it exhausts
the budget at roughly 25 000 total observations of the type, and that
failure is silent from the caller's side — `skipped.total`/`skipped.scanned`
come back `0` rather than the call failing. Independently, a low-selectivity
binding — most rows of the type failing the unit or condition filter, which
SQL still has to scan to reject — can exhaust the same budget in the row
query itself at as few as roughly 5 000 rows of the type. Dev deployments
that raise `SQLITE_OPERATION_BUDGET` (see the knowledge v2 runbook §1, which
sets it 50x higher) push both ceilings up by the same ~50x.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `HOST` | `0.0.0.0` | HTTP bind address |
| `PORT` | `8003` | HTTP bind port |
| `TAVILY_API_KEY` | required | Tavily credential, read only from the process environment |
| `BUNDLE_RECEIPT_KEY` | required | Separate secret of at least 32 bytes used to attest extraction checksums |
| `TAVILY_BASE_URL` | `https://api.tavily.com` | HTTPS Tavily provider endpoint |
| `REQUEST_TIMEOUT_SECONDS` | `120` | Outbound request timeout |
| `MAX_DOWNLOAD_BYTES` | `52428800` | Maximum accepted downloaded object size |
| `MAX_SEARCH_RESPONSE_BYTES` | `1048576` | Maximum accepted Tavily response size |
| `MAX_QUERY_ROWS` | `200` | Maximum rows returned by a collection query |
| `SQLITE_OPERATION_BUDGET` | `100000` | Maximum SQLite VM operations per collection read |
| `SQLITE_MAX_VALUE_BYTES` | `1048576` | Maximum size of one SQLite query value |
| `CALCULATOR_SCAN_BUDGET` | `1000000` | Maximum SQLite VM operations for `validate_calculator_spec`'s skip-reason scan (bounded to 20 000 rows), separate from `SQLITE_OPERATION_BUDGET` |
| `ALLOW_INSECURE_LOOPBACK` | `false` | Test-only opt-in for plain HTTP loopback URLs |
| `OBJECT_STORAGE_ALLOWED_ORIGINS` | empty | Comma-separated exact origins accepted for signed object GET/PUT |

Provider and presigned object URLs are credential-bearing destinations. The
service accepts HTTPS URLs and never returns or logs URL query strings. Plain
HTTP requires `ALLOW_INSECURE_OBJECT_STORAGE=true`; the integrated Oil MCP
deployment enables it for internal object storage. Signed object operations
remain disabled until their exact origins are listed in
`OBJECT_STORAGE_ALLOWED_ORIGINS`; paths, queries, and unlisted ports do not
widen that allowlist. Prefer HTTPS outside trusted networks. Downloads reject
transport content encoding so byte limits and checksums apply to the exact
stored bytes.
The receipt key never appears in tool arguments or results; all service replicas
must use the same independently generated value so receipts survive restarts.
Each filesystem-backed call must use `temporary_call_directory()` so its files are
isolated and removed when the call exits. The production image also disables
FastMCP's update check so startup does not add PyPI to the allowed egress
destinations.

## Test and build

Build the Dockerfile's test stage and run the contract suite against the
repository mounted read-only:

```shell
docker build --target test -f deploy/oil-mcp/domain-research.Dockerfile -t domain-research-test .
docker run --rm -v "$PWD:/workspace:ro" domain-research-test \
  python -m pytest mcp/domain-research/server/tests -q
```

Build the production image:

```shell
docker build --target runtime -f deploy/oil-mcp/domain-research.Dockerfile -t domain-research-mcp:test .
```
