FROM python:3.14.8-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

USER 65534:65534
# /metrics and /healthz of `radares run`; `radares health` asks /healthz, no extra packages
EXPOSE 9464
HEALTHCHECK --interval=1m --timeout=10s --start-period=2m --retries=3 \
    CMD ["/app/.venv/bin/radares", "health"]
ENTRYPOINT ["/app/.venv/bin/radares"]
CMD ["run"]
