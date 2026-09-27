import os


DOCLING_MCP_URL = os.getenv(
    "DOCLING_MCP_URL",
    "http://localhost:8000/mcp",
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8002"))

UPLOAD_TIMEOUT = float(
    os.getenv("UPLOAD_TIMEOUT", "300")
)

DOCLING_TIMEOUT = float(
    os.getenv("DOCLING_TIMEOUT", "300")
)