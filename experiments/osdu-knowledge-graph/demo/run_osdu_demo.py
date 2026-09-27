"""Run Q1-Q10 and one free question through the AppFactory OSDU agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from time import monotonic
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
GRAPH_DEMO_HELPERS = ROOT.parent.parent / "docs" / "graph-db-poc" / "demo"
SMOKE_DIR = ROOT / "smoke"
for path in (ROOT, GRAPH_DEMO_HELPERS, SMOKE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from graph_loader.controls import CONTROLS
from local_api import LocalApiError, LocalDemoError, LocalAppFactoryApi
from osdu_local_mongo import load_analysis_output
from verify_readonly_mcp import verify_readonly_mcp


LOGGER = logging.getLogger(__name__)
TENANT_ID = "osdu_demo"
WORKFLOW_ID = "osdu_graph_demo"
WORKFLOW_NODE_ID = "osdu_analyst"
POLL_SECONDS = 2
PROJECT_TIMEOUT_SECONDS = 180
SCENARIO_ATTEMPTS = 3
TERMINAL_FAILURES = {"failed", "cancelled", "canceled", "stopped", "error"}

FREE_QUESTION = (
    "Какова стоимость бурения у wellbore 15/9-19 SR (NPD-2105)?"
)


def _expected_from_control(control: Any) -> dict[str, Any]:
    """Map graph_loader gold rows to the E2E matcher shape."""
    payload = control.expected
    if control.id in {"Q1", "Q6", "Q9"}:
        return {"wellbores": [row["wellbore"] for row in payload]}
    if control.id == "Q2":
        return {"wellLogs": [row["wellLog"] for row in payload]}
    if control.id in {"Q3", "Q7"}:
        return {"well": payload[0]["well"], "wellbore": payload[0]["wellbore"]}
    if control.id in {"Q4", "Q5", "Q8"}:
        return {"osduId": payload[0]["osduId"]}
    if control.id == "Q10":
        return {row["label"]: int(row["count"]) for row in payload}
    raise ValueError(f"unsupported control {control.id}")


SCENARIOS = tuple(
    (control.id, control.question, _expected_from_control(control))
    for control in CONTROLS
) + (("FREE", FREE_QUESTION, None),)


async def wait_project(
    api: LocalAppFactoryApi,
    token: str,
    project_id: str,
    timeout_seconds: float = PROJECT_TIMEOUT_SECONDS,
) -> dict:
    deadline = monotonic() + timeout_seconds
    last_status = "unknown"
    while monotonic() < deadline:
        project = await api.get_project(token, project_id)
        last_status = str(project.get("status") or "unknown")
        if last_status == "completed":
            return project
        if last_status in TERMINAL_FAILURES:
            raise LocalDemoError(
                f"project {project_id} entered terminal status {last_status}"
            )
        await asyncio.sleep(min(POLL_SECONDS, max(0, deadline - monotonic())))
    raise LocalDemoError(
        f"project {project_id} timed out; last status: {last_status}"
    )


async def run_demo(
    api: LocalAppFactoryApi, *, environ: Mapping[str, str] = os.environ
) -> dict[str, Any]:
    credentials = _credentials(environ)
    token = await api.login(
        credentials["OSDU_DEMO_EMAIL"], credentials["OSDU_DEMO_PASSWORD"]
    )
    tools = await api.list_mcp_tools(token)
    readonly_wire_name = _readonly_wire_name(tools)
    scenario_results = []

    for scenario_id, question, expected in SCENARIOS:
        last_error: Exception | None = None
        for attempt in range(1, SCENARIO_ATTEMPTS + 1):
            project_id = ""
            try:
                project_id = await api.create_project(
                    token, prompt=question, workflow_id=WORKFLOW_ID
                )
                LOGGER.info(
                    "[OSDU_DEMO] scenario=%s attempt=%s/%s project_id=%s status=started",
                    scenario_id,
                    attempt,
                    SCENARIO_ATTEMPTS,
                    project_id,
                )
                await wait_project(api, token, project_id)
                messages = await api.get_messages(
                    token, project_id, include_journal=True
                )
                if scenario_id == "FREE":
                    _require_successful_ro_evidence(messages, tools)
                else:
                    _require_successful_read(messages, readonly_wire_name)
                output = await load_analysis_output(project_id, environ=environ)
                parsed = _normalize_output(output)
                _require_scenario_output(
                    scenario_id, parsed, expected, original=output
                )
            except (LocalDemoError, LocalApiError) as exc:
                last_error = (
                    exc if isinstance(exc, LocalDemoError) else LocalDemoError(str(exc))
                )
                message = str(exc)
                retryable = (
                    "tool arguments/Cypher" in message
                    or "output mismatch" in message
                    or "timed out" in message
                    or (scenario_id == "FREE" and "insufficient data" in message)
                )
                if not retryable or attempt >= SCENARIO_ATTEMPTS:
                    raise last_error
                LOGGER.warning(
                    "[OSDU_DEMO] scenario=%s attempt=%s failed (%s); retrying",
                    scenario_id,
                    attempt,
                    "tool-arg dump"
                    if "tool arguments/Cypher" in message
                    else "output mismatch"
                    if "output mismatch" in message
                    else "timeout"
                    if "timed out" in message
                    else "FREE phrasing",
                )
                continue

            scenario_results.append(
                {
                    "id": scenario_id,
                    "projectId": project_id,
                    "attempt": attempt,
                    "verified": True,
                }
            )
            LOGGER.info(
                "[OSDU_DEMO] scenario=%s project_id=%s status=completed",
                scenario_id,
                project_id,
            )
            break
        else:
            raise last_error or LocalDemoError(
                f"{scenario_id} failed after {SCENARIO_ATTEMPTS} attempts"
            )

    mcp_report = await verify_readonly_mcp()
    return {"scenarios": scenario_results, "mcp": mcp_report}


def _readonly_wire_name(rows: list[dict]) -> str:
    for row in rows:
        if (
            row.get("mcp_server") == "osdu-graph-ro"
            and row.get("rpc_name") == "query_graph_readonly"
            and isinstance(row.get("name"), str)
        ):
            return row["name"]
    raise LocalDemoError("cannot resolve OSDU read-only MCP wire name")


def _normalize_output(output: Any) -> Any:
    if isinstance(output, dict) and set(output) == {"raw_output"}:
        output = output["raw_output"]
    if isinstance(output, str):
        text = output.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:])
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        extracted = _extract_json_value(text)
        return extracted if extracted is not None else text
    return output


def _parse_output(output: Any) -> Any:
    """Compatibility wrapper used by unit tests."""
    return _normalize_output(output)


def _extract_json_value(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def _normalize_match_text(value: str) -> str:
    """Make LLM punctuation comparable to ASCII graph names."""
    text = unicodedata.normalize("NFKC", value)
    for dash in (
        "\u2010",  # hyphen
        "\u2011",  # non-breaking hyphen
        "\u2012",  # figure dash
        "\u2013",  # en dash
        "\u2014",  # em dash
        "\u2212",  # minus sign
    ):
        text = text.replace(dash, "-")
    text = re.sub(r"[\u00a0\u202f\u2007\u2009\u200a\u2008]", " ", text)
    text = re.sub(r"\s*\(([^)]+)\)", r" \1", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _searchable_text(value: Any) -> str:
    if isinstance(value, str):
        return _normalize_match_text(value)
    try:
        return _normalize_match_text(json.dumps(value, ensure_ascii=False))
    except TypeError:
        return _normalize_match_text(str(value))


def _collect_strings(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        text = _normalize_match_text(value).strip()
        if text:
            found.add(text)
        return found
    if isinstance(value, dict):
        for item in value.values():
            found.update(_collect_strings(item))
        return found
    if isinstance(value, list):
        for item in value:
            found.update(_collect_strings(item))
    return found


_TOKEN_BOUNDARY = r"[A-Za-z0-9]"


def _bounded_token_pattern(needle: str) -> str:
    """Match needle as a whole token; SR is not a prefix of SR2."""
    return rf"(?<!{_TOKEN_BOUNDARY}){re.escape(needle)}(?!{_TOKEN_BOUNDARY})"


def _contains_all(haystack: str, needles: list[str]) -> bool:
    """Require each distinct needle as a bounded token, longest first.

    Word boundaries stop ``SR`` matching inside ``SR2`` / ``Well`` inside
    ``Wellbore``. Longest-first consumption also stops ``15/9-F-1`` matching
    inside ``15/9-F-1 A`` (space is a boundary, so prefix names need the
    longer span to be taken first).
    """
    remaining = _normalize_match_text(haystack)
    unique = list(
        dict.fromkeys(_normalize_match_text(needle) for needle in needles if needle)
    )
    for needle in sorted(unique, key=len, reverse=True):
        match = re.search(_bounded_token_pattern(needle), remaining)
        if match is None:
            return False
        remaining = (
            remaining[: match.start()]
            + (" " * (match.end() - match.start()))
            + remaining[match.end() :]
        )
    return True


def _has_label_count(haystack: str, label: str, count: int) -> bool:
    """True when ``label`` is bound to ``count``, not merely both present.

    Allows a short filler word (``11 узлов Well``) but not a comma-separated
    neighbour (``Wellbore 11, Well 27`` must not pair Well with 11).
    """
    text = _normalize_match_text(haystack)
    token = _bounded_token_pattern(_normalize_match_text(label))
    number = _bounded_token_pattern(str(count))
    gap = r"[\s:='\-]*(?:[A-Za-zА-Яа-яЁё]+[\s:='\-]*){0,3}"
    return (
        re.search(number + gap + token, text) is not None
        or re.search(token + gap + number, text) is not None
    )


def _looks_like_insufficient_data(text: str) -> bool:
    """True when the answer says the asked property is not in the graph.

    Russian and English explicit absence both pass. A price/value is not a
    match. Comma is not a bridge: "нет, стоимость 1000" / "No, cost is 1000"
    must not count as missing data.
    """
    lowered = text.casefold()
    markers = (
        "недостат",
        "отсутств",
        "нет данных",
        "нет свойства",
        "нет информации",
        "не найден",
        "не найдено",
        "не в графе",
        "не содержится",
        "не содержит",
        "не хранится",
        "не хранит",
        "not stored",
        "is not stored",
        "isn't stored",
        "does not store",
        "doesn't store",
        "not in the graph",
        "not present",
        "not available",
        "no such property",
        "no data",
        "not found",
        "cannot find",
        "could not find",
        "couldn't find",
        "does not have",
        "doesn't have",
        "no property",
        "missing",
        "insufficient",
        "absent",
    )
    if any(marker in lowered for marker in markers):
        return True
    return (
        re.search(
            r"(?:нет|отсутств\w*|недостат\w*|не\s+найден\w*)[\s\w]{0,40}"
            r"(?:стоим\w*|цен[аыуе]\w*|свойств\w*|данн\w*|информац\w*)"
            r"|(?:стоим\w*|цен[аыуе]\w*|свойств\w*)[\s\w]{0,40}"
            r"(?:не\s+хран\w*|не\s+содерж\w*|отсутств\w*|нет)"
            r"|(?:no|not|missing|absent)[\s\w]{0,40}"
            r"(?:cost|price|propert\w*|data|information)"
            r"|(?:cost|price|propert\w*)[\s\w]{0,40}"
            r"(?:not\s+stor\w*|not\s+present|absent|missing|no)",
            lowered,
        )
        is not None
    )


def _looks_like_tool_arg_dump(actual: Any) -> bool:
    if isinstance(actual, dict):
        keys = {str(key) for key in actual.keys()}
        if "graphName" in keys and keys & {"query", "label"}:
            return True
        if keys and keys.issubset(
            {"graphName", "query", "params", "parameters", "label", "sampleSize"}
        ):
            return "query" in keys or "graphName" in keys or "label" in keys
        return False
    if isinstance(actual, str):
        stripped = actual.strip()
        if "graphName" in stripped and (
            '"query"' in stripped or '"label"' in stripped
        ):
            return True
        return False
    return False


def _source_text(actual: Any, original: Any | None) -> str:
    """Prefer the original agent string; JSON extraction must not hide it."""
    if isinstance(original, dict) and isinstance(original.get("raw_output"), str):
        return original["raw_output"]
    if isinstance(original, str):
        return original
    if isinstance(actual, str):
        return actual
    return ""


def _require_scenario_output(
    scenario_id: str,
    actual: Any,
    expected: dict[str, Any] | None,
    original: Any | None = None,
) -> None:
    source = _source_text(actual, original)

    if scenario_id == "FREE":
        if source and _looks_like_insufficient_data(_searchable_text(source)):
            return
        if isinstance(actual, dict):
            if (
                actual.get("answer") in (None, "")
                and actual.get("insufficientData") is True
                and isinstance(actual.get("missing"), str)
                and actual["missing"].strip()
            ):
                return
            if _looks_like_tool_arg_dump(actual):
                raise LocalDemoError(
                    f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
                )
            raise LocalDemoError(
                "free-question output did not explicitly identify insufficient data"
            )
        if _looks_like_tool_arg_dump(actual) or _looks_like_tool_arg_dump(source):
            raise LocalDemoError(
                f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
            )
        if _looks_like_insufficient_data(_searchable_text(actual)):
            return
        raise LocalDemoError(
            "free-question output did not explicitly identify insufficient data"
        )

    text = _searchable_text(source) if source else _searchable_text(actual)
    strings = _collect_strings(actual)

    if expected is None:
        raise LocalDemoError(f"{scenario_id} expected values are missing")

    if scenario_id in {"Q1", "Q2", "Q6", "Q9"}:
        key = "wellLogs" if scenario_id == "Q2" else "wellbores"
        names = [_normalize_match_text(name) for name in expected[key]]
        if _contains_all(text, names) or set(names).issubset(strings):
            return
        if _looks_like_tool_arg_dump(actual) or _looks_like_tool_arg_dump(source):
            raise LocalDemoError(
                f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
            )
        raise LocalDemoError(
            f"{scenario_id} output mismatch: expected names {names}, got {actual}"
        )

    if scenario_id in {"Q3", "Q7"}:
        well = _normalize_match_text(expected["well"])
        wellbore = _normalize_match_text(expected["wellbore"])
        if _contains_all(text, [well, wellbore]) or (
            well in strings and wellbore in strings
        ):
            return
        if _looks_like_tool_arg_dump(actual) or _looks_like_tool_arg_dump(source):
            raise LocalDemoError(
                f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
            )
        raise LocalDemoError(
            f"{scenario_id} output mismatch: expected {expected}, got {actual}"
        )

    if scenario_id in {"Q4", "Q5", "Q8"}:
        osdu_id = _normalize_match_text(expected["osduId"])
        if _contains_all(text, [osdu_id]) or osdu_id in strings:
            return
        if _looks_like_tool_arg_dump(actual) or _looks_like_tool_arg_dump(source):
            raise LocalDemoError(
                f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
            )
        raise LocalDemoError(
            f"{scenario_id} output mismatch: expected {expected}, got {actual}"
        )

    if scenario_id == "Q10":
        labels = ["Well", "Wellbore", "WellLog"]
        if all(
            _has_label_count(text, label, expected[label]) for label in labels
        ):
            return
        if isinstance(actual, dict):
            normalized = {
                str(key): int(value)
                for key, value in actual.items()
                if str(key) in expected and isinstance(value, (int, float, str))
            }
            if all(
                normalized.get(label) == expected[label] for label in labels
            ):
                return
        if _looks_like_tool_arg_dump(actual) or _looks_like_tool_arg_dump(source):
            raise LocalDemoError(
                f"{scenario_id} returned tool arguments/Cypher instead of an answer: {actual}"
            )
        raise LocalDemoError(
            f"{scenario_id} output mismatch: expected {expected}, got {actual}"
        )

    raise LocalDemoError(f"unsupported scenario {scenario_id}")


_RO_RPC_NAMES = {
    "query_graph_readonly",
    "get_node_schema",
}


def _require_successful_read(messages: list[dict], wire_name: str) -> None:
    if not _has_successful_tool(messages, {wire_name}):
        raise LocalDemoError("project has no successful OSDU read-only MCP call")


def _require_successful_ro_evidence(messages: list[dict], tool_rows: list[dict]) -> None:
    """FREE may stop after schema when the asked property is absent."""
    wire_names = {
        row["name"]
        for row in tool_rows
        if row.get("mcp_server") == "osdu-graph-ro"
        and row.get("rpc_name") in _RO_RPC_NAMES
        and isinstance(row.get("name"), str)
    }
    if not wire_names or not _has_successful_tool(messages, wire_names):
        raise LocalDemoError("project has no successful OSDU read-only MCP call")


def _has_successful_tool(messages: list[dict], wire_names: set[str]) -> bool:
    successful = {
        data.get("tool_call_id")
        for message in messages
        if message.get("type") == "tool_result"
        and message.get("status") == "ok"
        and isinstance((data := message.get("data")), dict)
        and data.get("name") in wire_names
    }
    return any(
        message.get("type") == "tool_call"
        and isinstance((data := message.get("data")), dict)
        and data.get("name") in wire_names
        and data.get("workflow_node_id") == WORKFLOW_NODE_ID
        and data.get("tool_call_id") in successful
        for message in messages
    )


def _credentials(environ: Mapping[str, str]) -> dict[str, str]:
    required = ("OSDU_DEMO_EMAIL", "OSDU_DEMO_PASSWORD")
    missing = [name for name in required if not environ.get(name, "").strip()]
    if missing:
        raise LocalDemoError(
            f"missing required environment variables: {', '.join(missing)}"
        )
    return {name: environ[name] for name in required}


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s | %(message)s"
    )
    api = LocalAppFactoryApi(
        "http://127.0.0.1:8010", timeout_seconds=PROJECT_TIMEOUT_SECONDS
    )
    try:
        result = await run_demo(api)
    finally:
        await api.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
