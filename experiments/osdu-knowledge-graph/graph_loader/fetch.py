"""Fetch the pinned Volve slice atomically from the public OSDU GitLab."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .version import data_version


LOGGER = logging.getLogger(__name__)
GITLAB_API = "https://community.opengroup.org/api/v4"
RETRIES = 3


def fetch_volve(output_dir: Path) -> dict[str, Any]:
    """Download and verify the immutable slice, then atomically publish it."""
    lock = data_version()
    output_dir = output_dir.resolve()
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    backup = output_dir.with_name(f".{output_dir.name}.backup")
    # Recover an interrupted publication before fetching a new snapshot.
    if backup.exists() and not output_dir.exists():
        backup.replace(output_dir)
    elif backup.exists():
        shutil.rmtree(backup)
    staging = Path(tempfile.mkdtemp(prefix=".osdu-volve-", dir=parent))
    inventory: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    try:
        for entity_type, root in lock["roots"].items():
            remote_paths = _list_json_files(root, lock["project"], lock["commit"])
            counts[entity_type] = len(remote_paths)
            expected = lock["expected_files"].get(entity_type)
            if len(remote_paths) != expected:
                raise RuntimeError(
                    f"{entity_type} inventory mismatch: expected {expected}, "
                    f"got {len(remote_paths)}"
                )
            for remote_path in remote_paths:
                content = _download_file(
                    remote_path, lock["project"], lock["commit"]
                )
                try:
                    json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"invalid JSON from {remote_path}") from exc
                relative = Path(remote_path).relative_to(lock["slice_root"])
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                inventory.append(
                    {
                        "entityType": entity_type,
                        "path": relative.as_posix(),
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
                LOGGER.info("[OSDU_FETCH] entity=%s path=%s", entity_type, remote_path)

        snapshot = {
            "dataset": lock["dataset"],
            "project": lock["project"],
            "commit": lock["commit"],
            "sliceRoot": lock["slice_root"],
            "counts": counts,
            "files": sorted(inventory, key=lambda item: item["path"]),
        }
        (staging / "SNAPSHOT.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if backup.exists():
            shutil.rmtree(backup)
        if output_dir.exists():
            output_dir.replace(backup)
        try:
            staging.replace(output_dir)
        except Exception:
            if backup.exists() and not output_dir.exists():
                backup.replace(output_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return snapshot
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _list_json_files(root: str, project_name: str, commit: str) -> list[str]:
    project = quote(project_name, safe="")
    paths: list[str] = []
    page = "1"
    while page:
        query = urlencode(
            {
                "path": root,
                "ref": commit,
                "recursive": "true",
                "per_page": 100,
                "page": page,
            }
        )
        body, headers = _request(
            f"{GITLAB_API}/projects/{project}/repository/tree?{query}"
        )
        entries = json.loads(body)
        if not isinstance(entries, list):
            raise RuntimeError(f"unexpected GitLab tree response for {root}")
        paths.extend(
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and str(entry.get("path", "")).lower().endswith(".json")
        )
        page = headers.get("X-Next-Page", "")
    return sorted(set(paths))


def _download_file(remote_path: str, project_name: str, commit: str) -> bytes:
    project = quote(project_name, safe="")
    path = quote(remote_path, safe="")
    query = urlencode({"ref": commit})
    body, _ = _request(
        f"{GITLAB_API}/projects/{project}/repository/files/{path}/raw?{query}"
    )
    return body


def _request(url: str) -> tuple[bytes, Any]:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = Request(url, headers={"User-Agent": "AppFactory-OSDU-demo/1.0"})
            with urlopen(request, timeout=60) as response:
                return response.read(), response.headers
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(2**attempt)
    raise RuntimeError("public OSDU GitLab request failed") from last_error
