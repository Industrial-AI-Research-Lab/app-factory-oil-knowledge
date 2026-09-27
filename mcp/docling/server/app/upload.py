import httpx

from .config import UPLOAD_TIMEOUT


async def upload_markdown(
    upload_url: str,
    markdown: str,
) -> None:
    """
    Upload markdown content to a presigned S3 PUT URL.

    The URL itself contains the authorization information,
    so no AWS credentials are required.
    """

    async with httpx.AsyncClient(
        timeout=UPLOAD_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.put(
            upload_url,
            content=markdown.encode("utf-8"),
            headers={
                "Content-Type": "text/markdown",
            },
        )

        response.raise_for_status()