FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
# The fixture generator writes files. The data service runs it; this image only reads.
RUN rm -f ./src/format_adapters_mcp/generate_fixtures.py
ENV PYTHONPATH=/app/src

RUN python -c "from format_adapters_mcp.server import mcp"

# AppFactory ZIP preflight → suggested_mode: stdio (no EXPOSE, no host port)
CMD ["python", "-m", "format_adapters_mcp", "--transport", "stdio"]
