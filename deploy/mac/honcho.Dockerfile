# DejaView wrapper Dockerfile for Honcho (task M2.4).
# The upstream Dockerfile at third_party/honcho/Dockerfile runs `uv sync` against
# the default PyPI index, whose file CDN (files.pythonhosted.org) is unreachable
# from inside the Docker Desktop VM in CN. Rather than patch the submodule, we
# rebuild the same stages here and rewrite the locked URLs to the Tsinghua mirror.
#
# Why rewrite URLs (not UV_INDEX_URL): uv.lock embeds absolute per-file URLs with
# hashes. Under `--frozen`, uv downloads from those exact URLs and ignores index
# overrides. The Tsinghua mirror mirrors PyPI's CDN under the SAME path
# (/packages/xx/yy/...), so swapping the host makes the locked hashes still match.
#
# Build context = repo root (compose.honcho.yml sets context: ../..), so all COPY
# paths are prefixed with third_party/honcho/. The submodule tree stays pristine.

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_HTTP_TIMEOUT=300

# CRITICAL: the host shell exports HTTP(S)_PROXY=http://127.0.0.1:7897 (a local
# Clash/Mihomo), and Docker Desktop passes these through to build containers.
# Inside the container, 127.0.0.1 is the container itself -> every HTTPS request
# is "Connection refused". We have no LAN-reachable proxy (host proxy binds to
# 127.0.0.1 only), and the VM can reach PyPI directly, so drop the proxy vars.
ENV HTTP_PROXY=
ENV HTTPS_PROXY=
ENV http_proxy=
ENV https_proxy=
ENV ALL_PROXY=
ENV all_proxy=

# Copy the lockfile + manifest first, then rewrite locked URLs to the Tsinghua
# mirror so `uv sync --frozen` can actually fetch the wheels from CN.
COPY third_party/honcho/uv.lock third_party/honcho/pyproject.toml /app/

RUN sed -i 's|https://files.pythonhosted.org|https://pypi.tuna.tsinghua.edu.cn|g' /app/uv.lock && \
    grep -c 'pypi.tuna.tsinghua.edu.cn' /app/uv.lock | xargs -I{} echo "rewrote {} URLs to tsinghua mirror"

# Install the project's dependencies using the rewritten lockfile.
# unset in-command: ENV=empty is not enough for some clients, so scrub the host's
# proxy exports inline right before uv runs.
RUN --mount=type=cache,target=/root/.cache/uv \
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY ; \
    uv sync --frozen --no-install-project --no-group dev

# Sync the project (full install)
RUN --mount=type=cache,target=/root/.cache/uv \
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY ; \
    uv sync --frozen --no-group dev
# Fix typing_extensions compatibility with pydantic_core on Python 3.13
RUN uv pip install --no-deps "typing-extensions>=4.15.0"

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME=/app
ENV UV_CACHE_DIR=/tmp/uv-cache

# Create non-root user and set ownership
RUN addgroup --system app && adduser --system --group app && mkdir -p /tmp/uv-cache && chown -R app:app /app /tmp/uv-cache

COPY --chown=app:app third_party/honcho/src/ /app/src/
COPY --chown=app:app third_party/honcho/migrations/ /app/migrations/
COPY --chown=app:app third_party/honcho/scripts/ /app/scripts/
COPY --chown=app:app third_party/honcho/docker/ /app/docker/
COPY --chown=app:app third_party/honcho/alembic.ini /app/alembic.ini
# Copy config files - this will copy config.toml if it exists, and config.toml.example
COPY --chown=app:app third_party/honcho/config.toml* /app/

# Switch to non-root user
USER app

EXPOSE 8000

CMD ["fastapi", "run", "--host", "0.0.0.0", "src/main.py"]
