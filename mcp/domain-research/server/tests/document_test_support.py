from app.documents.index import index_documents_impl


async def build_single_document(
    httpx_mock,
    payload: bytes,
    *,
    content_type: str,
    filename: str,
) -> bytes:
    httpx_mock.add_response(url="https://objects.test/source", content=payload)
    httpx_mock.add_response(method="PUT", url="https://objects.test/index-upload")
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
    return next(
        request.content
        for request in reversed(httpx_mock.get_requests())
        if request.method == "PUT"
    )
