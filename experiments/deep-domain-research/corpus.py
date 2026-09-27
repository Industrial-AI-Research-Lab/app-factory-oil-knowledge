import logging

from command_io import CommandError, confined, decode_json, digest, read_bytes, required

LOG = logging.getLogger(__name__)


def load_manifest(inputs, request):
    path = confined(inputs, required(request, "manifest"))
    raw = read_bytes(path)
    manifest = decode_json(raw, path)
    if not isinstance(manifest, dict):
        raise CommandError("INVALID_INPUT", "Manifest must be an object")
    if (manifest.get("corpus_id"), manifest.get("version")) != (
        request["corpus_id"],
        request["corpus_version"],
    ):
        raise CommandError(
            "VERSION_MISMATCH", "Manifest corpus/version differs from request"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise CommandError("INVALID_INPUT", "Manifest files must be a non-empty list")
    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise CommandError("INVALID_INPUT", "Manifest file must be an object")
        name = required(entry, "path")
        if name in seen:
            raise CommandError("INVALID_INPUT", "Duplicate manifest path", path=name)
        seen.add(name)
        payload = read_bytes(confined(path.parent, name))
        if digest(payload) != required(entry, "sha256"):
            raise CommandError(
                "CHECKSUM_MISMATCH", "File differs from manifest", path=name
            )
    LOG.info(
        "[B04] project_id=%s run_id=%s corpus_id=%s version=%s file_count=%d — inputs verified",
        request["project_id"],
        request["run_id"],
        request["corpus_id"],
        request["corpus_version"],
        len(files),
    )
    return manifest, path.parent, digest(raw)


def select_record(matches, identifier):
    if not matches:
        raise CommandError(
            "UNKNOWN_ID", "ID not found in declared source files", source_id=identifier
        )
    if len(matches) != 1:
        raise CommandError("DUPLICATE_ID", "ID is ambiguous", source_id=identifier)
    return matches[0]


def read_fragment(manifest, root, request):
    identifier = required(request, "source_id")
    matches = []
    for entry in manifest["files"]:
        name = entry["path"]
        if entry.get("role") == "fact_source":
            raw = read_bytes(confined(root, name))
            records = [
                decode_json(line, f"{name}:{n}")
                for n, line in enumerate(raw.splitlines(), 1)
                if line.strip()
            ]
            key = "es_id"
        elif entry.get("publication_id") is not None:
            records = [decode_json(read_bytes(confined(root, name)), name)]
            key = "publication_id"
        else:
            LOG.warning(
                "[B04] project_id=%s run_id=%s path=%s role=%s — skipped non-fact file",
                request["project_id"],
                request["run_id"],
                name,
                entry.get("role"),
            )
            continue
        for record in records:
            if not isinstance(record, dict):
                raise CommandError(
                    "INVALID_INPUT", "Source record must be an object", path=name
                )
            required(record, key)
            if record[key] == identifier:
                required(record, "text")
                matches.append({"record": record, "source_path": name})
    return select_record(matches, identifier)


def read_term(manifest, root, request):
    name = required(request, "terms_path")
    identifier = required(request, "term_id")
    # Resolve even undeclared paths to report escape attempts explicitly.
    path = confined(root, name)
    if not any(
        e["path"] == name and e.get("role") == "terminology" for e in manifest["files"]
    ):
        raise CommandError(
            "INVALID_INPUT",
            "Term file must be declared with role=terminology",
            path=name,
        )
    value = decode_json(read_bytes(path), path)
    terms = value.get("terms") if isinstance(value, dict) else None
    if not isinstance(terms, list) or not terms:
        raise CommandError(
            "INVALID_INPUT", "Expected a non-empty terms list", path=name
        )
    matches = []
    for term in terms:
        if not isinstance(term, dict):
            raise CommandError("INVALID_INPUT", "Term must be an object", path=name)
        if required(term, "id") == identifier:
            required(term, "name")
            required(term, "source")
            matches.append({"record": term, "source_path": name})
    return select_record(matches, identifier)
