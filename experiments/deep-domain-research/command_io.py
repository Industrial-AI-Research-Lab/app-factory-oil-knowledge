"""Explicit JSON errors, confined paths and replayable UTF-8 text results."""

import hashlib
import json
import re
from pathlib import Path


class CommandError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.error = {"code": code, "message": message, **details}


def required(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError("INVALID_INPUT", "Expected a non-empty string", field=key)
    return value


def component(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value
    ):
        raise CommandError("INVALID_PATH", "Expected a simple ID or result name")
    return value


def confined(root, relative):
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or "\x00" in relative
    ):
        raise CommandError("INVALID_PATH", "Expected a relative POSIX path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or ":" in relative:
        raise CommandError("INVALID_PATH", "Path escapes its root", path=relative)
    root = root.resolve()
    target = root / path
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise CommandError(
                "INVALID_PATH", "Symlinks are not supported", path=relative
            )
    if not target.resolve().is_relative_to(root):
        raise CommandError("INVALID_PATH", "Path escapes its root", path=relative)
    return target


def read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise CommandError(
            "MISSING_FILE", "Required file is missing", path=str(path)
        ) from exc


def decode_json(data, path):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CommandError(
            "INVALID_JSON", "Expected valid UTF-8 JSON", path=str(path)
        ) from exc


def read_json(path):
    return decode_json(read_bytes(path), path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_text(request, inputs, results):
    inputs, results = inputs.resolve(), results.resolve()
    if results.is_relative_to(inputs) or inputs.is_relative_to(results):
        raise CommandError("INVALID_PATH", "Inputs and results must be disjoint roots")
    name = component(required(request, "name"))
    text = required(request, "text").replace("\r\n", "\n").replace("\r", "\n")
    payload = text.encode("utf-8")
    relative = f'{request["project_id"]}/{request["run_id"]}/{name}.txt'
    path = confined(results, relative)
    meta_path = confined(
        results, str(Path(relative).with_suffix(".json")).replace("\\", "/")
    )
    metadata = {
        "request": request,
        "sha256": digest(payload),
        "bytes": len(payload),
        "encoding": "UTF-8",
        "newlines": "LF",
    }
    if path.exists() or meta_path.exists():
        if not path.exists() or not meta_path.exists():
            raise CommandError(
                "RESULT_CONFLICT", "Incomplete result; use a new name", path=relative
            )
        if read_json(meta_path) != metadata or read_bytes(path) != payload:
            raise CommandError(
                "RESULT_CONFLICT", "Existing result is immutable", path=relative
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(payload)
        with meta_path.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(metadata, file, ensure_ascii=False, indent=2)
            file.write("\n")
    stored = read_bytes(path)
    return {
        "path": relative,
        "metadata_path": meta_path.relative_to(results).as_posix(),
        "sha256": digest(stored),
        "bytes": len(stored),
        "encoding": "UTF-8",
        "newlines": "LF",
    }
