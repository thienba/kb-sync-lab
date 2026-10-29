FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
COPY main.py ask.py ./
COPY prompts ./prompts
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "main.py"]
