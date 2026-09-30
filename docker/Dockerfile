# Test images for the Linux half of the platform matrix.
#
# GitHub's ubuntu runner is one Linux: glibc, a UTF-8 locale, a writable root and
# no container mounts. These images cover the Linuxes it is not -- musl, a POSIX
# locale, a read-only root, and real /run/secrets and ConfigMap mounts.
#
# macOS and Windows are deliberately absent: neither can run in a Linux
# container, and emulation would test the emulator. Those live in CI.
ARG BASE=python:3.12-slim
FROM ${BASE}

# Pinned, and kept in step with .pre-commit-config.yaml's uv-pre-commit rev:
# the workspace root declares `required-version = ">=0.12"`.
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

# The environment lives outside the mounted repo: the host's .venv is macOS or
# Windows and must never be reused here.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /repo
