FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
# Bake the tiktoken encoding so a read-only, offline container never downloads it.
RUN TIKTOKEN_CACHE_DIR=/opt/tiktoken /app/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
ARG GIT_SHA=unknown
ENV PYTHONUNBUFFERED=1 \
    OUT_DIR=/tmp/out \
    PUBLIC_DIR=/public \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    GIT_SHA=${GIT_SHA} \
    PATH="/app/.venv/bin:$PATH"
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /public && chown 10001:10001 /public
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /opt/tiktoken /opt/tiktoken
COPY src ./src
COPY main.py ask.py ./
COPY prompts ./prompts
USER 10001
ENTRYPOINT ["python", "main.py"]
