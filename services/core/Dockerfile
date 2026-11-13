# The core service image (deployment-aws.md §6.4): FastAPI + the Planner graph, built with uv
# from the workspace lockfile, linux/amd64, non-root, port 8000.
#   docker build -f services/core/Dockerfile -t mothusi-core .
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.14.6-slim-trixie
COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    MOTHUSI_DATA=/data
WORKDIR /app
# The workspace as it is laid out in the repo: mothusi_core finds packs/ and its fixtures relative to
# its own source (providers.ROOT), so the packages are installed editable in place.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY packs ./packs
RUN uv sync --frozen --no-dev --package mothusi-core \
    && useradd --uid 10001 --no-create-home mothusi \
    && mkdir -p /data && chown mothusi /data
COPY services/core/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
USER mothusi
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "mothusi_core.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
