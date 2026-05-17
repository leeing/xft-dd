FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

# Use Aliyun mirror for Debian packages
RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null \
    || true

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Chromium system deps + browser to shared path (runs as root, used by app user)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install-deps chromium \
    && playwright install chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/playwright-browsers

WORKDIR /app
COPY pyproject.toml uv.lock .env.example ./
COPY src/ ./src/
COPY config/ ./config/

RUN useradd --create-home app \
    && mkdir -p /app/runs /app/batch_runs \
    && chown -R app:app /app
USER app

ENV PLAYWRIGHT_CHROMIUM_HEADLESS=1

ENTRYPOINT ["xft"]
CMD ["--help"]
