# The knowledge service image (knowledge.md §6.4, deployment-aws.md §6.4): FastAPI + pgvector search
# with bge-m3 on CPU. The model's weights are downloaded once at build time into /models and the hub
# is forced offline at runtime, so a running container never fetches anything.
#   docker build -f services/knowledge/Dockerfile -t mothusi-knowledge .
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.14.6-slim-trixie
COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/models
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv sync --frozen --no-dev --package mothusi-knowledge \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')" \
    && useradd --uid 10001 --no-create-home mothusi \
    && chmod -R a+rX /models
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
# The same entrypoint as the core: POSTGRES_URL from the RDS secret's parts (deployment-aws.md §6.4).
COPY services/core/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
USER mothusi
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "mothusi_knowledge.api:app_from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000"]
