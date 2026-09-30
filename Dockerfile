FROM ghcr.io/astral-sh/uv:0.12.19 AS uv
FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY data/fixtures ./data/fixtures
COPY reports ./reports
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/var \
    && chown app:app /app/var

ENV PATH="/app/.venv/bin:${PATH}"
ENV MODELS_DIR=/opt/models
RUN reviews download-models

ENV HF_HUB_OFFLINE=1 \
    DISABLE_SAFETENSORS_CONVERSION=1 \
    DATABASE_URL=sqlite:////app/var/app.db

USER app
EXPOSE 8080
CMD ["python", "-m", "appstore_review_analysis.docker_entrypoint"]
