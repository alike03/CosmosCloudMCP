# syntax=docker/dockerfile:1
# Debian trixie ships Python 3.13. Unlike the python:* images it sets no extra
# environment variables, so the container env only shows PATH and your settings.
FROM debian:trixie-slim AS python
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

FROM python AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check . \
    && /opt/venv/bin/pip uninstall -y pip

# Run the unit tests: docker build --target test .
FROM build AS test
COPY tests ./tests
RUN /opt/venv/bin/python -m ensurepip > /dev/null \
    && /opt/venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check ".[dev]" \
    && /opt/venv/bin/pytest -q

# Runtime image (default target)
FROM python AS runtime
COPY --from=build /opt/venv /opt/venv
# Cosmos keeps a container's entrypoint, healthcheck and env when it updates the
# image, so containers created from 0.2.0 still call "cosmos-cloud-mcp" and
# "python" by name. Both must resolve via /usr/local/bin, which is on PATH in
# the old python:* env and in Debian's default PATH.
RUN ln -s /opt/venv/bin/cosmos-cloud-mcp /usr/local/bin/cosmos-cloud-mcp \
    && printf '#!/bin/sh\nexec /opt/venv/bin/python "$@"\n' > /usr/local/bin/python \
    && chmod 755 /usr/local/bin/python \
    && python -c "import cosmos_cloud_mcp.healthcheck" \
    && command -v cosmos-cloud-mcp \
    && useradd --system --uid 10001 --no-create-home mcp
USER mcp
LABEL cosmos-icon="https://raw.githubusercontent.com/alike03/CosmosCloudMCP/main/assets/logo.png"
# stdio by default; set COSMOS_MCP_TRANSPORT=http to serve MCP over HTTP on COSMOS_MCP_PORT
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "cosmos_cloud_mcp.healthcheck"]
ENTRYPOINT ["cosmos-cloud-mcp"]
