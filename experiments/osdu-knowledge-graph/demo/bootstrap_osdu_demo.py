"""Idempotently register the OSDU read-only MCP, analyst, and critic in AppFactory."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from typing import Mapping


GRAPH_DEMO_HELPERS = Path(__file__).resolve().parents[3] / "docs" / "graph-db-poc" / "demo"
if str(GRAPH_DEMO_HELPERS) not in sys.path:
    sys.path.insert(0, str(GRAPH_DEMO_HELPERS))

from local_api import LocalDemoError, LocalAppFactoryApi


TENANT_ID = "osdu_demo"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
DEFAULT_MODEL = "iairlab/gpt-oss-120b"
HTTP_TIMEOUT_SECONDS = 180.0
ANALYST_ID = "osdu_graph_analyst"
CRITIC_ID = "osdu_graph_critic"
PROMPT_FILES = {
    ANALYST_ID: PROMPTS_DIR / "osdu-graph-analyst.prompt.txt",
    CRITIC_ID: PROMPTS_DIR / "osdu-graph-critic.prompt.txt",
}
EXPECTED_MCP_PUBLIC_IDS = {
    f"osdu-graph-ro.{name}"
    for name in (
        "query_graph",
        "query_graph_readonly",
        "list_graphs",
        "delete_graph",
        "get_graph_schema",
        "get_node_schema",
        "get_relationship_schema",
    )
}
ALLOWED_ANALYST_TOOLS = {
    "osdu-graph-ro.query_graph_readonly",
    "osdu-graph-ro.get_node_schema",
}
REQUIRED_CREDENTIALS = (
    "AppFactory_ROOT_EMAIL",
    "AppFactory_ROOT_PASSWORD",
    "OSDU_DEMO_EMAIL",
    "OSDU_DEMO_PASSWORD",
)


def load_osdu_demo_bundle(
    bundle_path: Path = CONFIG_DIR / "osdu-demo-bundle.json",
    *,
    environ: Mapping[str, str] = os.environ,
) -> dict:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle = _with_prompts(bundle)
    bundle = _with_model_override(
        bundle, environ.get("OSDU_DEMO_MODEL") or DEFAULT_MODEL
    )
    _require_readonly_bundle(bundle)
    return bundle


async def run_bootstrap(
    api: LocalAppFactoryApi,
    *,
    environ: Mapping[str, str] = os.environ,
    cursor_path: Path = CONFIG_DIR / "osdu-mcp.cursor.json",
    bundle_path: Path = CONFIG_DIR / "osdu-demo-bundle.json",
) -> dict:
    credentials = _credentials(environ)
    cursor_text = cursor_path.read_text(encoding="utf-8")
    json.loads(cursor_text)
    bundle = load_osdu_demo_bundle(bundle_path, environ=environ)

    root_token = await api.login(
        credentials["AppFactory_ROOT_EMAIL"], credentials["AppFactory_ROOT_PASSWORD"]
    )
    await api.ensure_tenant(root_token, TENANT_ID, "OSDU Knowledge Graph Demo")
    await api.ensure_tenant_admin(
        root_token,
        email=credentials["OSDU_DEMO_EMAIL"],
        password=credentials["OSDU_DEMO_PASSWORD"],
        tenant_id=TENANT_ID,
    )
    tenant_token = await api.login(
        credentials["OSDU_DEMO_EMAIL"], credentials["OSDU_DEMO_PASSWORD"]
    )
    await api.import_cursor_json(tenant_token, cursor_text)
    public_ids = {_public_id(row) for row in await api.list_mcp_tools(tenant_token)}
    missing = EXPECTED_MCP_PUBLIC_IDS - public_ids
    if missing:
        raise LocalDemoError(
            f"missing OSDU MCP tools: {', '.join(sorted(missing))}"
        )

    preview = await api.dry_run_bundle(root_token, bundle, TENANT_ID)
    errors = _preview_errors(preview)
    if errors:
        raise LocalDemoError(f"bundle dry-run contains errors: {', '.join(errors)}")
    applied = await api.apply_bundle(root_token, bundle, TENANT_ID)
    print(f"OSDU MCP tools discovered: {len(EXPECTED_MCP_PUBLIC_IDS)}")
    print("OSDU analyst write tools: 0")
    print("OSDU critic MCP tools: 0")
    print("OSDU critic gate: REVISE retries analyst, up to 5 times")
    print(f"Bundle items applied: agent=2 workflow=1 tenant={TENANT_ID}")
    return {"public_ids": public_ids, "preview": preview, "applied": applied}


def _with_prompts(bundle: dict) -> dict:
    result = copy.deepcopy(bundle)
    for agent in result.get("items", {}).get("agents", []):
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("_id") or "")
        prompt_path = PROMPT_FILES.get(agent_id)
        if prompt_path is None:
            continue
        text = prompt_path.read_text(encoding="utf-8").strip()
        if not text:
            raise LocalDemoError(f"empty prompt file: {prompt_path}")
        agent["system_prompt"] = text
        agent.setdefault("model", DEFAULT_MODEL)
    return result


def _with_model_override(bundle: dict, model: str | None) -> dict:
    result = copy.deepcopy(bundle)
    chosen = (model or "").strip() or DEFAULT_MODEL
    for agent in result.get("items", {}).get("agents", []):
        if isinstance(agent, dict):
            agent["model"] = chosen
    return result


def _require_readonly_bundle(bundle: dict) -> None:
    agents = bundle.get("items", {}).get("agents", [])
    by_id = {
        str(row.get("_id")): row
        for row in agents
        if isinstance(row, dict) and row.get("_id")
    }
    if set(by_id) != {ANALYST_ID, CRITIC_ID}:
        raise LocalDemoError(
            "OSDU bundle must contain analyst and critic agents"
        )
    analyst = by_id[ANALYST_ID]
    critic = by_id[CRITIC_ID]
    allowed = set(analyst.get("allowed_mcp_tools") or [])
    if allowed != ALLOWED_ANALYST_TOOLS:
        raise LocalDemoError("OSDU analyst tool allowlist is not read-only")
    if list(critic.get("allowed_mcp_tools") or []):
        raise LocalDemoError("OSDU critic must not have MCP tools")
    if not str(analyst.get("system_prompt") or "").strip():
        raise LocalDemoError("OSDU analyst system_prompt is empty")
    if not str(critic.get("system_prompt") or "").strip():
        raise LocalDemoError("OSDU critic system_prompt is empty")
    workflows = bundle.get("items", {}).get("workflows") or []
    if not workflows:
        raise LocalDemoError("OSDU bundle must contain at least one workflow")
    workflow = workflows[0]
    node_ids = {
        str(node.get("id"))
        for node in workflow.get("nodes") or []
        if isinstance(node, dict)
    }
    if not {"osdu_analyst", "osdu_critic", "osdu_critic_gate"} <= node_ids:
        raise LocalDemoError(
            "OSDU workflow must include analyst, critic, and critic gate"
        )
    gate = next(
        node
        for node in workflow.get("nodes") or []
        if isinstance(node, dict) and node.get("id") == "osdu_critic_gate"
    )
    if gate.get("type") != "validator":
        raise LocalDemoError("OSDU critic gate must be a validator")
    if gate.get("max_reject_retries") != 5:
        raise LocalDemoError("OSDU critic gate must allow 5 revision rounds")
    checks = gate.get("checks") or []
    if not any(
        isinstance(check, dict)
        and str(check.get("key") or "").startswith("osdu_user_answer")
        for check in checks
    ):
        raise LocalDemoError("OSDU critic gate must check osdu_user_answer")
    critic_node = next(
        node
        for node in workflow.get("nodes") or []
        if isinstance(node, dict) and node.get("id") == "osdu_critic"
    )
    if "osdu_user_answer" not in (critic_node.get("writes") or []):
        raise LocalDemoError("OSDU critic must write osdu_user_answer")
    if "user_prompt" not in (critic_node.get("reads") or []):
        raise LocalDemoError("OSDU critic must read user_prompt")
    analyst_node = next(
        node
        for node in workflow.get("nodes") or []
        if isinstance(node, dict) and node.get("id") == "osdu_analyst"
    )
    if "user_prompt" not in (analyst_node.get("reads") or []):
        raise LocalDemoError("OSDU analyst must read user_prompt")
    edges = workflow.get("edges") or []
    analyst_to_critic = any(
        isinstance(edge, dict)
        and edge.get("from") == "osdu_analyst"
        and edge.get("to") == "osdu_critic"
        for edge in edges
    )
    critic_to_gate = any(
        isinstance(edge, dict)
        and edge.get("from") == "osdu_critic"
        and edge.get("to") == "osdu_critic_gate"
        for edge in edges
    )
    rejected = any(
        isinstance(edge, dict)
        and edge.get("from") == "osdu_critic_gate"
        and edge.get("to") == "osdu_analyst"
        and edge.get("condition") == "rejected"
        for edge in edges
    )
    approved = any(
        isinstance(edge, dict)
        and edge.get("from") == "osdu_critic_gate"
        and edge.get("to") == "end"
        and edge.get("condition") == "approved"
        for edge in edges
    )
    if not analyst_to_critic or not critic_to_gate or not rejected or not approved:
        raise LocalDemoError(
            "OSDU critic must review the analyst, then the gate must retry the analyst or publish to the user"
        )


def _credentials(environ: Mapping[str, str]) -> dict[str, str]:
    missing = [key for key in REQUIRED_CREDENTIALS if not environ.get(key, "").strip()]
    if missing:
        raise LocalDemoError(
            f"missing required environment variables: {', '.join(missing)}"
        )
    return {key: environ[key] for key in REQUIRED_CREDENTIALS}


def _public_id(row: dict) -> str:
    value = row.get("public_id")
    if isinstance(value, str):
        return value
    return f"{row.get('mcp_server', '')}.{row.get('rpc_name', '')}"


def _preview_errors(preview: dict) -> list[str]:
    return [
        f"{kind}: {row.get('error') or 'unknown error'}"
        for kind, rows in preview.get("items", {}).items()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and row.get("action") == "error"
    ]


async def _main() -> None:
    api = LocalAppFactoryApi(
        "http://127.0.0.1:8010", timeout_seconds=HTTP_TIMEOUT_SECONDS
    )
    try:
        await run_bootstrap(api)
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(_main())
