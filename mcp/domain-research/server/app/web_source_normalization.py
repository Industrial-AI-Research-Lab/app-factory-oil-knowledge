from __future__ import annotations

import hashlib
import logging
import re
from datetime import date

import httpx

from .errors import ToolFailure
from .models import MAX_SEARCH_TITLE_CHARS, MAX_SEARCH_URL_CHARS
from .publication_dates import publication_date
from .tavily_response import canonical_url

logger = logging.getLogger(__name__)
MAX_DATE_CHARS = 128
EXTRACT_FAILURE_CODES = frozenset(
    {
        "PROVIDER_FAILED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_RESPONSE_INVALID",
        "PROVIDER_RESPONSE_TOO_LARGE",
        "SOURCE_TOO_LARGE",
    }
)
_SOURCE_ID = re.compile(r"[0-9a-f]{64}")
_INVALID = "PROVIDER_RESPONSE_INVALID"
_TOO_LARGE = "PROVIDER_RESPONSE_TOO_LARGE"
_MISSING_ROW = "provider returned no row for this URL, e.g. it answered for a redirect"
_REPEATED_ROW = "provider returned more than one row for this URL"


def is_utf8(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode()
    except UnicodeEncodeError:
        return False
    return True


def sources_from_provider(
    payload: dict,
    requested_urls: set[str],
    *,
    tolerant_dates: bool = False,
    per_url_failures: bool = False,
    request_id: str = "-",
) -> tuple[list[dict], list[dict[str, str]]]:
    """With per_url_failures a bad, repeated or missing row fails only its own
    URL and nothing is raised for rows; without it any bad row, or no usable
    page at all, rejects the whole response."""
    rows = payload.get("results")
    failed_rows = payload.get("failed_results", [])
    if not isinstance(rows, list) or not isinstance(failed_rows, list):
        raise ToolFailure(_INVALID, "Provider response is invalid")
    if per_url_failures:
        return _sources_per_url(
            rows, failed_rows, requested_urls, tolerant_dates, request_id
        )
    if len(rows) + len(failed_rows) > len(requested_urls):
        raise ToolFailure(_INVALID, "Provider response is invalid")
    sources: list[dict] = []
    resolved: set[str] = set()
    for row in rows:
        if not _valid_success(row):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            )
        try:
            url = canonical_url(row["url"])
            published_date = publication_date(
                row.get("published_date"), tolerant=tolerant_dates
            )
        except (httpx.InvalidURL, KeyError, TypeError, ValueError):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            ) from None
        if (
            len(url) > MAX_SEARCH_URL_CHARS
            or url not in requested_urls
            or url in resolved
        ):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            )
        resolved.add(url)
        sources.append(_source(url, row, published_date))
    failures: list[dict[str, str]] = []
    for row in failed_rows:
        if not isinstance(row, dict) or not is_utf8(row.get("url")):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            )
        try:
            url = canonical_url(row["url"])
        except (httpx.InvalidURL, TypeError, ValueError):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            ) from None
        if (
            len(url) > MAX_SEARCH_URL_CHARS
            or url not in requested_urls
            or url in resolved
        ):
            raise ToolFailure(
                "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
            )
        resolved.add(url)
        failures.append({"url": url, "code": "PROVIDER_FAILED"})
    if resolved != requested_urls:
        raise ToolFailure("PROVIDER_RESPONSE_INVALID", "Provider response is invalid")
    if not sources:
        raise ToolFailure(
            "EXTRACTION_ALL_FAILED", "Extract provider returned no usable pages"
        )
    return sources, failures


def _valid_success(row: object) -> bool:
    return (
        isinstance(row, dict)
        and is_utf8(row.get("url"))
        and len(row["url"]) <= MAX_SEARCH_URL_CHARS
        and _row_problem(row) is None
    )


def _row_problem(row: dict) -> tuple[str, str] | None:
    title = row.get("title", "")
    published_date = row.get("published_date")
    if not is_utf8(row.get("raw_content")) or not row["raw_content"]:
        return _INVALID, "raw_content is missing or empty"
    if not is_utf8(title) or not (published_date is None or is_utf8(published_date)):
        return _INVALID, "title or published_date is not text"
    if len(title) > MAX_SEARCH_TITLE_CHARS:
        return _TOO_LARGE, f"title exceeds {MAX_SEARCH_TITLE_CHARS} characters"
    if published_date is not None and len(published_date) > MAX_DATE_CHARS:
        return _TOO_LARGE, f"published_date exceeds {MAX_DATE_CHARS} characters"
    return None


def _source(url: str, row: dict, published_date: date | None) -> dict:
    return {
        "source_id": hashlib.sha256(url.encode()).hexdigest(),
        "url": url,
        "title": row.get("title", ""),
        "published_date": (
            published_date.isoformat() if published_date is not None else None
        ),
        "text": row["raw_content"],
    }


def _sources_per_url(
    rows: list,
    failed_rows: list,
    requested_urls: set[str],
    tolerant_dates: bool,
    request_id: str,
) -> tuple[list[dict], list[dict[str, str]]]:
    found: dict[str, list[dict]] = {url: [] for url in requested_urls}
    ignored = 0
    for row in rows:
        url = _requested_url(row, requested_urls)
        if url is None:
            ignored += 1
        else:
            found[url].append(_page(row, url, tolerant_dates))
    for row in failed_rows:
        url = _requested_url(row, requested_urls)
        if url is None:
            ignored += 1
        else:
            found[url].append({"url": url, "code": "PROVIDER_FAILED"})
    if ignored:
        logger.warning(
            "[WEB_SNAPSHOT] request_id=%s ignored_rows=%d — provider rows name no requested URL and are skipped",
            request_id,
            ignored,
        )
    sources: list[dict] = []
    failures: list[dict[str, str]] = []
    for url in sorted(found):
        outcomes = found[url]
        if len(outcomes) > 1:
            failures.append(_failure(url, _INVALID, _REPEATED_ROW))
        elif not outcomes:
            failures.append(_failure(url, _INVALID, _MISSING_ROW))
        elif "code" in outcomes[0]:
            failures.append(outcomes[0])
        else:
            sources.append(outcomes[0])
    return sources, failures


def _requested_url(row: object, requested_urls: set[str]) -> str | None:
    if not isinstance(row, dict) or not is_utf8(row.get("url")):
        return None
    try:
        url = canonical_url(row["url"])
    except (httpx.InvalidURL, TypeError, UnicodeError, ValueError):
        return None
    return url if url in requested_urls else None


def _page(row: dict, url: str, tolerant_dates: bool) -> dict:
    problem = _row_problem(row)
    if problem is not None:
        return _failure(url, *problem)
    try:
        published_date = publication_date(
            row.get("published_date"), tolerant=tolerant_dates
        )
    except (TypeError, ValueError):
        return _failure(url, _INVALID, "published_date is not a date")
    return _source(url, row, published_date)


def _failure(url: str, code: str, detail: str) -> dict[str, str]:
    return {"url": url, "code": code, "detail": detail}


def valid_saved_failure(failure: object) -> bool:
    return (
        isinstance(failure, dict)
        and set(failure) in ({"url", "code"}, {"url", "code", "detail"})
        and isinstance(failure["url"], str)
        and isinstance(failure["code"], str)
        and failure["code"] in EXTRACT_FAILURE_CODES
        and isinstance(failure.get("detail", ""), str)
    )


def valid_saved_source(source: object) -> bool:
    if not isinstance(source, dict):
        return False
    source_id = source.get("source_id")
    url = source.get("url")
    title = source.get("title")
    published_date = source.get("published_date")
    if (
        not is_utf8(source_id)
        or _SOURCE_ID.fullmatch(source_id) is None
        or not is_utf8(url)
        or len(url) > MAX_SEARCH_URL_CHARS
        or not is_utf8(title)
        or len(title) > MAX_SEARCH_TITLE_CHARS
        or not is_utf8(source.get("text"))
    ):
        return False
    try:
        canonical = canonical_url(url)
    except (httpx.InvalidURL, TypeError, UnicodeError, ValueError):
        return False
    if canonical != url or source_id != hashlib.sha256(canonical.encode()).hexdigest():
        return False
    if published_date is None:
        return True
    if not is_utf8(published_date) or len(published_date) != 10:
        return False
    try:
        return date.fromisoformat(published_date).isoformat() == published_date
    except ValueError:
        return False
