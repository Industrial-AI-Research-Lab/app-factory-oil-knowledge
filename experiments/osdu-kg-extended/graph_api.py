"""Small FalkorDB Browser REST client with strict SSE handling."""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

TARGET_GRAPH = "osdu-volve-extended"
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GraphHttpError(RuntimeError):
    def __init__(self, status: int, detail: str, retry_after: str | None = None):
        super().__init__(f"FalkorDB HTTP {status}: {detail}")
        self.status = status
        try:
            self.retry_after = float(retry_after) if retry_after else None
        except ValueError:
            self.retry_after = None


def parse_sse(text: str) -> list[dict]:
    event = None
    payload: list[str] = []
    result = None
    for line in [*text.splitlines(), ""]:
        if line.startswith("event:"):
            event = line[6:].strip()
            payload = []
        elif line.startswith("data:"):
            payload.append(line[5:].lstrip(" "))
        elif not line and event:
            raw = "".join(payload)
            if event == "error":
                raise ValueError(f"FalkorDB query failed: {raw[:500]}")
            if event == "result":
                try:
                    decoded = json.loads(raw)
                    if not isinstance(decoded, dict) or "metadata" not in decoded:
                        raise TypeError("result envelope has no metadata")
                    result = decoded.get("data", [])
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError("Incomplete FalkorDB result stream") from exc
            event, payload = None, []
    if not isinstance(result, list):
        raise ValueError("FalkorDB response contained no result event")
    return result


def validate_target(name: str) -> str:
    if name != TARGET_GRAPH:
        raise ValueError(f"writes are allowed only to {TARGET_GRAPH!r}")
    return name


def cypher_literal(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite number")
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(cypher_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(_KEY.fullmatch(str(key)) for key in value):
            raise ValueError("invalid Cypher property key")
        return "{" + ",".join(f"{key}:{cypher_literal(val)}" for key, val in value.items()) + "}"
    raise TypeError(f"unsupported Cypher value: {type(value).__name__}")


class GraphClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        body = json.dumps(
            {"username": username, "password": password, "name": "extended-graph-loader",
             "ttlSeconds": 3600, "host": "localhost", "port": "6379", "tls": "false"}
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/auth/tokens/credentials", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            self.token = json.load(response)["token"]

    def query(self, graph: str, cypher: str, timeout: int = 30000) -> list[dict]:
        params = urllib.parse.urlencode({"query": cypher, "timeout": timeout})
        request = urllib.request.Request(
            f"{self.base_url}/api/graph/{urllib.parse.quote(graph, safe='')}?{params}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=max(60, timeout / 1000 + 10)) as response:
                return parse_sse(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise GraphHttpError(exc.code, detail, exc.headers.get("Retry-After")) from exc
