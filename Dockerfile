# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Run the unit tests: docker build --target test .
FROM base AS test
COPY tests ./tests
RUN pip install ".[dev]" && pytest -q

# Runtime image (default target): MCP server on stdio
FROM base AS runtime
RUN pip install . \
    && rm -rf /app/src \
    && useradd --system --uid 10001 --no-create-home mcp
USER mcp
# stdio by default; set COSMOS_MCP_TRANSPORT=http to serve MCP over HTTP on COSMOS_MCP_PORT
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "cosmos_cloud_mcp.healthcheck"]
ENTRYPOINT ["cosmos-cloud-mcp"]
