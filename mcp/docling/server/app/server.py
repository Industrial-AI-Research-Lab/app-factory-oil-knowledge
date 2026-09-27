import logging
import re
from fastmcp import FastMCP, Client
from fastmcp.server import create_proxy

from .config import (
    DOCLING_MCP_URL,
    DOCLING_TIMEOUT, HOST, PORT
)
from .upload import upload_markdown

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)
logger = logging.getLogger(__name__)

docling_proxy = create_proxy(
    DOCLING_MCP_URL,
    name="Docling MCP",
)

mcp = FastMCP(
    name="Docling Wrapper",
)

mcp.mount(docling_proxy)

def extract_markdown(result) -> str:
    """
    Extract only markdown text from an MCP CallToolResult.

    Expected Docling response:
    {
        "document_key": "...",
        "markdown": "..."
    }

    The function deliberately ignores metadata such as document_key.
    """

    data = getattr(result, "data", None)

    if data is not None:
        if isinstance(data, dict):
            markdown = data.get("markdown")

            if isinstance(markdown, str):
                return markdown

            value = data.get("result")

            if isinstance(value, str):
                return value

        elif isinstance(data, str):
            return data

    text_parts = []

    for content in getattr(result, "content", []):
        text = getattr(content, "text", None)

        if isinstance(text, str):
            text_parts.append(text)

    if text_parts:
        text = "".join(text_parts)

        try:
            import json

            parsed = json.loads(text)

            if isinstance(parsed, dict):
                markdown = parsed.get("markdown")

                if isinstance(markdown, str):
                    return markdown
        except (json.JSONDecodeError, TypeError):
            pass

        return text

    raise RuntimeError(
        "export_docling_document_to_markdown "
        "returned no textual markdown content"
    )

def clean_markdown(markdown: str) -> str:
    markdown = re.sub(
        r"<!--\s*image\s*-->",
        "",
        markdown,
    )

    markdown = re.sub(
        r"\n{3,}",
        "\n\n",
        markdown,
    )

    return markdown.strip()

@mcp.tool
async def export_docling_document_to_markdown_and_upload(
    document_key: str,
    upload_url: str,
    max_size: int | None = None,
) -> dict:
    """
    Export a Docling document from the Docling local cache to Markdown
    and upload the Markdown to a presigned S3 PUT URL.

    Args:
        document_key:
            Unique identifier of the document in the Docling local cache.

        upload_url:
            Presigned S3 PUT URL where the Markdown file will be uploaded.

        max_size:
            Optional maximum number of Markdown characters to export.
            If omitted, the complete Markdown document is exported.

    Returns:
        Upload status and basic metadata. The Markdown itself is not returned.
    """

    if max_size is not None and max_size <= 0:
        raise ValueError("max_size must be greater than 0")

    logger.info(
        "[export_and_upload] START "
        "document_key=%s max_size=%s",
        document_key,
        max_size,
    )

    async with Client(DOCLING_MCP_URL) as client:

        result = await client.call_tool(
            "export_docling_document_to_markdown",
            {
                "document_key": document_key,
                "max_size": max_size,
            },
            timeout=DOCLING_TIMEOUT,
        )

        logger.info(
            "[export_and_upload] Docling tool returned "
            "is_error=%s",
            result.is_error,
        )

    if result.is_error:
        error_text = "\n".join(
            getattr(content, "text", str(content))
            for content in result.content
        )

        raise RuntimeError(
            "Docling export_docling_document_to_markdown failed: "
            f"{error_text}"
        )

    logger.info(
        "[export_and_upload] Extracting Markdown from Docling result"
    )

    markdown = extract_markdown(result)
    markdown = clean_markdown(markdown)

    logger.info(
        "[export_and_upload] Markdown extracted "
        "characters=%d bytes=%d",
        len(markdown),
        len(markdown.encode("utf-8")),
    )

    if not markdown:
        raise RuntimeError(
            "Docling export_docling_document_to_markdown "
            "returned an empty Markdown document"
        )

    logger.info(
        "[export_and_upload] Starting upload to S3 "
        "document_key=%s size_bytes=%d",
        document_key,
        len(markdown.encode("utf-8")),
    )

    await upload_markdown(
        upload_url=upload_url,
        markdown=markdown,
    )

    logger.info(
        "[export_and_upload] Upload completed successfully "
        "document_key=%s",
        document_key,
    )

    return {
        "status": "success",
        "uploaded": True,
        "document_key": document_key,
        "size_bytes": len(markdown.encode("utf-8")),
    }



if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=HOST,
        port=PORT,
    )
