from datetime import date

from command_io import (
    CommandError,
    component,
    confined,
    decode_json,
    digest,
    read_bytes,
    required,
)
from corpus import load_manifest


def object_value(value, field):
    if not isinstance(value, dict):
        raise CommandError("INVALID_INPUT", "Expected an object", field=field)
    return value


def array_value(value, field, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise CommandError("INVALID_INPUT", "Expected an array", field=field)
    return value


def strings(value, field, nonempty=False):
    values = array_value(value, field, nonempty)
    for item in values:
        required({field: item}, field)
    if len(set(values)) != len(values):
        raise CommandError("INVALID_INPUT", "Duplicate values", field=field)
    return values


def calendar_date(value, field):
    required({field: value}, field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(
            "INVALID_INPUT", "Expected an ISO date", field=field
        ) from exc
    if parsed.isoformat() != value:
        raise CommandError("INVALID_INPUT", "Expected YYYY-MM-DD", field=field)
    return value


def context(request):
    object_value(request, "request")
    result = {
        key: required(request, key)
        for key in ("operation", "project_id", "run_id", "corpus_id", "corpus_version")
    }
    component(result["project_id"])
    component(result["run_id"])
    return result


def read_verified_json(root, entry):
    name = required(entry, "path")
    path = confined(root, name)
    raw = read_bytes(path)
    if digest(raw) != required(entry, "sha256"):
        raise CommandError(
            "CHECKSUM_MISMATCH", "Consumed file differs from manifest", path=name
        )
    return decode_json(raw, path)


def dictionary(root, manifest, filename, field):
    entry = next(
        (entry for entry in manifest["files"] if entry["path"] == filename), None
    )
    if entry is None:
        raise CommandError(
            "MISSING_FILE", "Dictionary is not in manifest", path=filename
        )
    value = object_value(read_verified_json(root, entry), filename)
    required(value, "version")
    records = array_value(value.get(field), field, True)
    ids = []
    for record in records:
        object_value(record, field)
        ids.append(required(record, "id"))
    if len(set(ids)) != len(ids):
        raise CommandError(
            "DUPLICATE_ID", "Dictionary IDs are ambiguous", path=filename
        )
    return value


def load_inputs(request, inputs):
    context(request)
    manifest, root, checksum = load_manifest(inputs, request)
    publications = []
    for entry in manifest["files"]:
        if "publication_id" not in entry:
            continue
        record = object_value(read_verified_json(root, entry), "publication")
        for key in ("publication_id", "title"):
            required(record, key)
        for key in ("text", "url", "source"):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise CommandError("INVALID_INPUT", "Expected text or null", field=key)
        if record["publication_id"] != entry["publication_id"]:
            raise CommandError("INVALID_INPUT", "Publication ID differs from manifest")
        calendar_date(record.get("published_date"), "published_date")
        publications.append(record)
    if not publications:
        raise CommandError("INVALID_INPUT", "Manifest declares no publications")
    ids = [record["publication_id"] for record in publications]
    if len(set(ids)) != len(ids):
        raise CommandError("DUPLICATE_ID", "Publication IDs are ambiguous")
    term_request = object_value(request.get("terminology"), "terminology")
    for key in ("manifest", "corpus_id", "corpus_version"):
        required(term_request, key)
    term_request = request | term_request
    term_manifest, term_root, term_hash = load_manifest(inputs, term_request)
    terms = dictionary(term_root, term_manifest, "terms.json", "terms")
    taxonomy = dictionary(term_root, term_manifest, "news-taxonomy.json", "categories")
    provenance = {
        "news": {
            "corpus_id": manifest["corpus_id"],
            "version": manifest["version"],
            "manifest": request["manifest"],
            "sha256": checksum,
        },
        "terminology": {
            "corpus_id": term_manifest["corpus_id"],
            "version": term_manifest["version"],
            "manifest": term_request["manifest"],
            "sha256": term_hash,
            "terms_version": terms["version"],
            "taxonomy_version": taxonomy["version"],
        },
    }
    return publications, terms, taxonomy, provenance
