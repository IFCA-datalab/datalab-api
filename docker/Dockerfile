FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so they are cached across code changes
COPY pyproject.toml uv.lock README.rst LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY datalab_api ./datalab_api
RUN uv sync --locked --no-dev

USER nobody
EXPOSE 8000
CMD ["uvicorn", "--factory", "datalab_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
