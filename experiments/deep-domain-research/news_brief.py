from command_io import CommandError, component, required
from news_inputs import calendar_date, object_value, strings
from news_storage import store_result


def prepare_brief(request, records, provenance, results):
    brief = object_value(request.get("brief"), "brief")
    for key in ("version", "query", "topic", "source_policy"):
        required(brief, key)
    component(brief["version"])
    period = object_value(brief.get("period"), "period")
    for key in ("start", "end"):
        calendar_date(period.get(key), key)
    if period["start"] > period["end"]:
        raise CommandError("INVALID_INPUT", "Period start is after end")
    strings(brief.get("inclusions"), "inclusions", True)
    strings(brief.get("exclusions"), "exclusions")
    strings(brief.get("output_formats"), "output_formats", True)
    dates = [record["published_date"] for record in records]
    value = brief | {
        "kind": "brief",
        "project_id": request["project_id"],
        "run_id": request["run_id"],
        "inputs": provenance,
        "archive_period": {"start": min(dates), "end": max(dates)},
        "publication_count": len(records),
    }
    reference = store_result(value, request, results, f'brief-{brief["version"]}')
    return {"brief": value, "reference": reference}
