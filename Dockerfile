FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TERM=xterm-256color \
    STUDY_SKIP_AUTO_SETUP=1 \
    STUDY_DOCS_DIR=/app/demo \
    STUDY_TUI_DEMO_FILE=/app/demo/leph101.pdf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE requirements.txt ./
COPY assets ./assets
COPY demo ./demo
COPY scripts ./scripts
COPY src ./src

RUN pip install --no-cache-dir -e .
RUN npm install -g wetty@2.0.2

EXPOSE 7860

CMD ["wetty", "--host", "0.0.0.0", "--port", "7860", "--command", "bash /app/scripts/launch-hosted-demo.sh"]
