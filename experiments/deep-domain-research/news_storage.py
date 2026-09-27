import json
import os
import tempfile
from pathlib import Path

from command_io import (
    CommandError,
    component,
    confined,
    decode_json,
    digest,
    read_bytes,
)
from news_inputs import object_value


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def roots(inputs, results):
    inputs, results = inputs.resolve(), results.resolve()
    if inputs.is_relative_to(results) or results.is_relative_to(inputs):
        raise CommandError("INVALID_PATH", "Inputs and results must be disjoint")


def store_result(value, request, results, name):
    relative = f'{request["project_id"]}/{request["run_id"]}/{component(name)}.json'
    path = confined(results, relative)
    payload = encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".news-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path) != payload:
                raise CommandError(
                    "RESULT_CONFLICT", "Existing result is immutable", path=relative
                )
    finally:
        Path(temporary).unlink()
    stored = read_bytes(path)
    if stored != payload:
        raise CommandError("RESULT_CONFLICT", "Stored result differs", path=relative)
    return {"path": relative, "sha256": digest(stored)}


def read_result(reference, request, results, provenance, kind):
    object_value(reference, "reference")
    path = confined(results, reference.get("path"))
    expected_root = confined(results, f'{request["project_id"]}/{request["run_id"]}')
    if path.parent != expected_root:
        raise CommandError("INVALID_PATH", "Reference belongs to another project/run")
    raw = read_bytes(path)
    if digest(raw) != reference.get("sha256"):
        raise CommandError("CHECKSUM_MISMATCH", "Result differs from reference")
    value = object_value(decode_json(raw, path), "stored result")
    if value.get("kind") != kind:
        raise CommandError(
            "INVALID_INPUT", "Reference has the wrong result kind", field="kind"
        )
    if (
        value.get("project_id") != request["project_id"]
        or value.get("run_id") != request["run_id"]
    ):
        raise CommandError("VERSION_MISMATCH", "Result project/run differs")
    if value.get("inputs") != provenance:
        raise CommandError("VERSION_MISMATCH", "Result refers to different inputs")
    return value
