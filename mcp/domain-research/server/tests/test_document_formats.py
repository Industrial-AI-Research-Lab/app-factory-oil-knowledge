import pytest
from app.documents import DocumentSpec
from app.documents.formats import parse_document
from app.errors import ToolFailure


@pytest.mark.parametrize(
    ("filename", "content_type", "payload"),
    [
        (
            "source.json",
            "application/json",
            b'{"text":"valid","value":1e10000}',
        ),
        (
            "source.jsonl",
            "application/x-ndjson",
            b'{"text":"valid","value":1e10000}\n',
        ),
    ],
)
def test_rejects_number_outside_finite_float_range(
    filename,
    content_type,
    payload,
):
    spec = DocumentSpec(
        source_id="source",
        filename=filename,
        content_type=content_type,
        url="https://objects.test/source",
        metadata={},
    )

    with pytest.raises(ToolFailure, match="DOCUMENT_FORMAT_INVALID"):
        parse_document(spec, payload)


def test_accepts_largest_finite_float():
    spec = DocumentSpec(
        source_id="source",
        filename="source.json",
        content_type="application/json",
        url="https://objects.test/source",
        metadata={},
    )

    fragments = parse_document(
        spec,
        b'{"text":"valid","value":1.7976931348623157e308}',
    )

    assert fragments[0].metadata == {"value": 1.7976931348623157e308}
