ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24
FROM ${BUILD_FROM}

# Runtime only. The AI CLIs are NOT part of the image: the app installs the
# enabled ones with their vendors' official installers into /data at runtime.
RUN apk add --no-cache \
        bash \
        curl \
        libgcc \
        libstdc++ \
        ripgrep \
        tar \
        ttyd

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

ARG BUILD_VERSION=dev
ENV AI_USAGE_VERSION=${BUILD_VERSION} \
    HOME=/data/home \
    PATH=/data/home/.local/bin:${PATH} \
    PYTHONPATH=/opt/ai_usage \
    DISABLE_AUTOUPDATER=1 \
    AGY_CLI_DISABLE_AUTO_UPDATE=true \
    USE_BUILTIN_RIPGREP=0 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

COPY rootfs /
