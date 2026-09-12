# syntax=docker/dockerfile:1
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy UV_NO_CACHE=1 \
    # FastMCP keeps the OAuth proxy's on-disk state under this folder (see docs/administration.md)
    FASTMCP_HOME=/data/fastmcp
WORKDIR /app

# Dependencies first (layer cached until the lock changes), then the package itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY powerbi_mcp/ powerbi_mcp/
RUN uv sync --frozen --no-dev --no-editable

# Example skills; a deployment overlays /app/skills with its own (deploy/build_context.ps1).
COPY skills/ skills/

ARG VERSION=dev
LABEL org.opencontainers.image.title="powerbi-mcp-gateway" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/PlainsightPro/powerbi-mcp-gateway" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --create-home --uid 1000 gateway && mkdir -p /data/fastmcp && chown -R gateway:gateway /data
USER gateway
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["python", "-m", "powerbi_mcp"]
