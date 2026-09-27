from command_io import CommandError, required
from news_inputs import array_value, object_value, strings

DECISIONS = {"include", "exclude", "review", "insufficient_evidence"}
LEVELS = {"statement", "laboratory", "field", "operational", "not_established"}


def categories(value, valid):
    selected = strings(value, "categories")
    if not set(selected) <= valid:
        raise CommandError("UNKNOWN_ID", "Unknown taxonomy category")
    return selected


def evidence(value, records, publication_id=None, nonempty=False):
    entries = array_value(value, "evidence", nonempty)
    for item in entries:
        object_value(item, "evidence")
        identifier = publication_id or required(item, "publication_id")
        if (
            publication_id
            and "publication_id" in item
            and item["publication_id"] != publication_id
        ):
            raise CommandError(
                "INVALID_EVIDENCE", "Quote attribution differs from publication"
            )
        if identifier not in records:
            raise CommandError("UNKNOWN_ID", "Unknown evidence publication")
        if required(item, "quote") not in records[identifier]["text"]:
            raise CommandError(
                "INVALID_EVIDENCE",
                "Quote is absent from archived text",
                publication_id=identifier,
            )
    return entries


def validate_analysis(value, selected, records, taxonomy):
    object_value(value, "analysis")
    publications = array_value(value.get("publications"), "publications")
    events = array_value(value.get("events"), "events")
    valid_categories = {item["id"] for item in taxonomy["categories"]}
    decisions = {}
    for row in publications:
        object_value(row, "publication decision")
        identifier = required(row, "publication_id")
        if identifier not in selected:
            raise CommandError(
                "UNKNOWN_ID", "Decision is not a candidate", publication_id=identifier
            )
        if identifier in decisions:
            raise CommandError("DUPLICATE_ID", "Duplicate publication decision")
        decision = required(row, "decision")
        if decision not in DECISIONS:
            raise CommandError("INVALID_INPUT", "Unknown publication decision")
        required(row, "reason")
        assigned_categories = categories(row.get("categories"), valid_categories)
        evidence(row.get("evidence"), records, identifier, decision == "include")
        strings(
            row.get("limitations"),
            "limitations",
            decision == "include" and not assigned_categories,
        )
        decisions[identifier] = row
    if set(decisions) != set(selected):
        raise CommandError(
            "INCOMPLETE_ANALYSIS", "Every candidate requires one decision"
        )
    event_ids, memberships = set(), {identifier: set() for identifier in decisions}
    event_contents = []
    for event in events:
        object_value(event, "event")
        identifier = required(event, "event_id")
        if identifier in event_ids:
            raise CommandError("DUPLICATE_ID", "Duplicate event ID")
        event_ids.add(identifier)
        content = {key: value for key, value in event.items() if key != "event_id"}
        if content in event_contents:
            raise CommandError(
                "DUPLICATE_EVENT", "Identical event content has multiple IDs"
            )
        event_contents.append(content)
        for key in ("description", "technology", "task", "merge_reason"):
            required(event, key)
        for key in ("object", "time"):
            if key not in event:
                raise CommandError(
                    "INVALID_INPUT", "Missing nullable event field", field=key
                )
            if event[key] is not None:
                required(event, key)
        if required(event, "evidence_level") not in LEVELS:
            raise CommandError("INVALID_INPUT", "Unknown evidence level")
        assigned_categories = categories(event.get("categories"), valid_categories)
        strings(event.get("limitations"), "limitations", not assigned_categories)
        sources = strings(event.get("publication_ids"), "publication_ids", True)
        for source in sources:
            if source not in decisions or decisions[source]["decision"] != "include":
                raise CommandError(
                    "INVALID_EVIDENCE", "Event requires included publications"
                )
            memberships[source].add(identifier)
        quotes = evidence(event.get("evidence"), records, nonempty=True)
        if {item["publication_id"] for item in quotes} != set(sources):
            raise CommandError(
                "INVALID_EVIDENCE", "Every event source requires its own quote"
            )
    for identifier, decision in decisions.items():
        if decision["decision"] == "include" and not memberships[identifier]:
            raise CommandError(
                "INVALID_EVIDENCE", "Included publication has no supported event"
            )
    return decisions, memberships


def validate_critique(value, initial, analysis, records):
    object_value(value, "critique")
    cycle = value.get("correction_cycle")
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle not in (0, 1):
        raise CommandError("INVALID_INPUT", "At most one correction cycle is allowed")
    if initial != analysis and cycle != 1:
        raise CommandError(
            "INVALID_INPUT", "Changed analysis requires correction_cycle=1"
        )
    event_ids = set()
    for draft in (initial, analysis):
        object_value(draft, "analysis")
        array_value(draft.get("publications"), "publications")
        for event in array_value(draft.get("events"), "events"):
            event_ids.add(required(object_value(event, "event"), "event_id"))
    seen = set()
    for note in array_value(value.get("notes"), "notes"):
        object_value(note, "note")
        identifier = required(note, "id")
        if identifier in seen:
            raise CommandError("DUPLICATE_ID", "Duplicate critique note")
        seen.add(identifier)
        for key in ("issue", "action", "resolution_reason"):
            required(note, key)
        if required(note, "resolution") not in {"resolved", "unresolved"}:
            raise CommandError("INVALID_INPUT", "Unknown critique resolution")
        for key, valid in (("publication_ids", set(records)), ("event_ids", event_ids)):
            if not set(strings(note.get(key), key)) <= valid:
                raise CommandError("UNKNOWN_ID", "Unknown critique target", field=key)
