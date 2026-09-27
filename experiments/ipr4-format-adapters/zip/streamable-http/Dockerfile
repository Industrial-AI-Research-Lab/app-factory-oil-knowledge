FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
# The fixture generator writes files. The data service runs it; this image only reads.
RUN rm -f ./src/format_adapters_mcp/generate_fixtures.py
ENV PYTHONPATH=/app/src
ENV PORT=8080
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8080
ENV SEGY_BASE_URL=http://10.0.15.21:18090
ENV WITSML_BASE_URL=http://10.0.15.21:18090
ENV SCADA_BASE_URL=http://10.0.15.21:18090

RUN python -c "from format_adapters_mcp.server import mcp; from format_adapters_mcp import segy_reader, witsml_reader, scada_reader"

EXPOSE 8080

# HTTP image. The stdio image is stdio/Dockerfile; ZIP import only offers a file named Dockerfile.
CMD ["python", "-m", "format_adapters_mcp", "--transport", "streamable-http"]
