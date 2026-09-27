from datetime import date

import pytest
from app.models import WebSearchRequest
from pydantic import ValidationError


@pytest.mark.parametrize("query", [None, "", "   ", "oil\x00gas", "x" * 401])
def test_search_request_rejects_invalid_query(query):
    with pytest.raises(ValidationError):
        WebSearchRequest(query=query)


def test_search_request_rejects_reversed_dates():
    with pytest.raises(ValidationError, match="start_date must not be after end_date"):
        WebSearchRequest(
            query="polymer flooding",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 3, 31),
        )


@pytest.mark.parametrize(
    ("include_domains", "exclude_domains"),
    [
        (["spe.org", "SPE.ORG"], []),
        (["spe.org"], [" spe.org "]),
    ],
)
def test_search_request_rejects_duplicate_domain_filters(
    include_domains,
    exclude_domains,
):
    with pytest.raises(
        ValidationError, match="domain filters must not contain duplicates"
    ):
        WebSearchRequest(
            query="polymer flooding",
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )


@pytest.mark.parametrize("max_results", [0, 21, True, 1.5, "5"])
def test_search_request_rejects_invalid_max_results(max_results):
    with pytest.raises(ValidationError):
        WebSearchRequest(query="polymer flooding", max_results=max_results)


@pytest.mark.parametrize("max_results", [1, 20])
def test_search_request_accepts_max_results_boundaries(max_results):
    request = WebSearchRequest(
        query="polymer flooding",
        max_results=max_results,
    )

    assert request.max_results == max_results


@pytest.mark.parametrize("exact_match", [None, 0, 1, "true"])
def test_search_request_rejects_non_boolean_exact_match(exact_match):
    with pytest.raises(ValidationError):
        WebSearchRequest(
            query="polymer flooding",
            exact_match=exact_match,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("include_domains", [""]),
        ("include_domains", ["spe.org\nbad"]),
        ("include_domains", ["x" * 254]),
        ("include_domains", [f"example-{index}.test" for index in range(21)]),
        ("exclude_domains", [f"example-{index}.test" for index in range(21)]),
    ],
)
def test_search_request_rejects_invalid_domain_list(field, value):
    with pytest.raises(ValidationError):
        WebSearchRequest(query="polymer flooding", **{field: value})
