import hashlib
import json
from pathlib import Path

import pytest
from app.documents.index import index_documents_impl
from app.documents.search import (
    read_document_fragments_impl,
    search_documents_impl,
)
from app.errors import ToolFailure
from document_test_support import build_single_document

KNOWLEDGE_ROOT = (
    Path(__file__).parents[4]
    / "experiments"
    / "deep-domain-research"
    / "data"
    / "knowledge"
)


@pytest.mark.asyncio
async def test_document_search_returns_exact_saved_fragment(httpx_mock):
    sources = [
        {
            "source_id": "chunks",
            "filename": "chunks.jsonl",
            "content_type": "application/x-ndjson",
            "url": "https://objects.test/chunks",
        },
        {
            "source_id": "documents",
            "filename": "documents.csv",
            "content_type": "text/csv",
            "url": "https://objects.test/documents",
        },
        {
            "source_id": "brief",
            "filename": "brief.md",
            "content_type": "text/markdown",
            "url": "https://objects.test/brief",
        },
    ]
    fixture_paths = {
        "chunks.jsonl": KNOWLEDGE_ROOT / "chunks.jsonl",
        "documents.csv": KNOWLEDGE_ROOT / "documents.csv",
        "brief.md": KNOWLEDGE_ROOT / "auxiliary" / "brief.md",
    }
    for source in sources:
        httpx_mock.add_response(
            url=source["url"],
            content=fixture_paths[source["filename"]].read_bytes(),
        )
    httpx_mock.add_response(
        method="PUT",
        url="https://objects.test/index-upload",
    )

    indexed = await index_documents_impl(
        sources,
        "https://objects.test/index-upload",
    )

    uploaded_index = next(
        request.content
        for request in httpx_mock.get_requests()
        if request.method == "PUT"
    )
    httpx_mock.add_response(
        url="https://objects.test/index-download",
        content=uploaded_index,
    )
    hits = await search_documents_impl(
        "https://objects.test/index-download",
        "interfacial tension",
        limit=5,
    )
    httpx_mock.add_response(
        url="https://objects.test/index-download",
        content=uploaded_index,
    )
    fragments = await read_document_fragments_impl(
        "https://objects.test/index-download",
        [hits.results[0].fragment_id],
    )

    assert indexed.document_count == 3
    assert indexed.fragment_count >= 116
    assert indexed.schema_version == 1
    assert indexed.sha256 == hashlib.sha256(uploaded_index).hexdigest()
    assert "interfacial tension" in fragments.fragments[0].text.casefold()
    assert "interfacial tension" in hits.results[0].excerpt.casefold()
    assert fragments.fragments[0].source_id == hits.results[0].source_id
    line_number = int(fragments.fragments[0].locator.removeprefix("line:"))
    expected = json.loads(
        fixture_paths["chunks.jsonl"]
        .read_text(encoding="utf-8")
        .splitlines()[line_number - 1]
    )["text"]
    assert fragments.fragments[0].text == expected
    assert fragments.fragments[0].truncated is False
    assert fragments.fragments[0].metadata["document_id"]
    assert fragments.fragments[0].metadata == hits.results[0].metadata
    expected_fragment_id = hashlib.sha256(
        b"\x00".join(
            (
                b"chunks",
                fragments.fragments[0].locator.encode(),
                expected.encode(),
            )
        )
    ).hexdigest()
    assert fragments.fragments[0].fragment_id == expected_fragment_id


@pytest.mark.asyncio
async def test_index_rejects_duplicate_source_ids():
    document = {
        "source_id": "duplicate",
        "filename": "source.txt",
        "content_type": "text/plain",
        "url": "https://objects.test/source",
    }

    with pytest.raises(ToolFailure, match="DOCUMENT_SOURCE_DUPLICATE"):
        await index_documents_impl(
            [document, {**document, "url": "https://objects.test/other"}],
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error_code"),
    [(b"", "DOCUMENT_EMPTY"), (b"\xff", "DOCUMENT_ENCODING_INVALID")],
)
async def test_index_rejects_empty_or_malformed_utf8(
    httpx_mock,
    payload,
    error_code,
):
    httpx_mock.add_response(content=payload)

    with pytest.raises(ToolFailure, match=error_code):
        await index_documents_impl(
            [
                {
                    "source_id": "source",
                    "filename": "source.txt",
                    "content_type": "text/plain",
                    "url": "https://objects.test/source",
                }
            ],
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("source.pdf", "application/pdf"),
        (
            "source.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
async def test_index_rejects_unsupported_document_types(filename, content_type):
    with pytest.raises(ToolFailure, match="DOCUMENT_TYPE_UNSUPPORTED"):
        await index_documents_impl(
            [
                {
                    "source_id": "source",
                    "filename": filename,
                    "content_type": content_type,
                    "url": "https://objects.test/source",
                }
            ],
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
async def test_csv_formula_is_stored_as_text(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"name,formula\ncalculation,=2+3\n",
        content_type="text/csv",
        filename="source.csv",
    )
    httpx_mock.add_response(content=index_bytes)
    hits = await search_documents_impl("https://objects.test/index", "calculation")
    httpx_mock.add_response(content=index_bytes)
    result = await read_document_fragments_impl(
        "https://objects.test/index",
        [hits.results[0].fragment_id],
    )

    assert result.fragments[0].text == "name: calculation\nformula: =2+3"


@pytest.mark.asyncio
async def test_jsonl_error_reports_physical_line(httpx_mock):
    httpx_mock.add_response(content=b'{"text":"valid"}\nnot-json\n')

    with pytest.raises(ToolFailure, match="line:2"):
        await index_documents_impl(
            [
                {
                    "source_id": "source",
                    "filename": "source.jsonl",
                    "content_type": "application/x-ndjson",
                    "url": "https://objects.test/source",
                }
            ],
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
async def test_jsonl_rejects_nonstandard_numeric_constant(httpx_mock):
    httpx_mock.add_response(content=b'{"text":"valid","value":NaN}\n')

    with pytest.raises(ToolFailure, match="line:1"):
        await index_documents_impl(
            [
                {
                    "source_id": "source",
                    "filename": "source.jsonl",
                    "content_type": "application/x-ndjson",
                    "url": "https://objects.test/source",
                }
            ],
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
async def test_index_rejects_documents_above_total_download_limit(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", "10")
    httpx_mock.add_response(url="https://objects.test/one", content=b"123456")
    httpx_mock.add_response(url="https://objects.test/two", content=b"abcdef")
    documents = [
        {
            "source_id": name,
            "filename": f"{name}.txt",
            "content_type": "text/plain",
            "url": f"https://objects.test/{name}",
        }
        for name in ("one", "two")
    ]

    with pytest.raises(ToolFailure, match="DOCUMENTS_TOO_LARGE"):
        await index_documents_impl(
            documents,
            "https://objects.test/index-upload",
        )


@pytest.mark.asyncio
async def test_index_does_not_store_signed_source_url(httpx_mock):
    source_url = "https://objects.test/source?X-Amz-Signature=secret-token"
    httpx_mock.add_response(url=source_url, content=b"public document text")
    httpx_mock.add_response(method="PUT", url="https://objects.test/index-upload")

    await index_documents_impl(
        [
            {
                "source_id": "source",
                "filename": "source.txt",
                "content_type": "text/plain",
                "url": source_url,
            }
        ],
        "https://objects.test/index-upload",
    )

    uploaded_index = next(
        request.content
        for request in httpx_mock.get_requests()
        if request.method == "PUT"
    )
    assert b"secret-token" not in uploaded_index
