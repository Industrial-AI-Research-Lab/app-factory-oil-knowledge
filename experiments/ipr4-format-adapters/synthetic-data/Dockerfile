FROM python:3.11-slim

WORKDIR /app

COPY zip/streamable-http/src/format_adapters_mcp/formats.py /app/formats.py
COPY zip/streamable-http/src/format_adapters_mcp/generate_fixtures.py /app/generate_fixtures.py
COPY zip/streamable-http/src/format_adapters_mcp/scada_mock.py /app/scada_mock.py
COPY synthetic-data/serve.py /app/serve.py

RUN python /app/generate_fixtures.py --out /data

ENV DATA_DIR=/data
ENV DATA_PORT=8090
EXPOSE 8090

CMD ["python", "/app/serve.py"]
