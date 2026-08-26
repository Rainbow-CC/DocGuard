# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    HOME=/tmp

# LibreOffice, Poppler, and Pillow are required by the DOCX preprocessing skill.
# Noto CJK prevents common Chinese text rendering failures in generated images.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libreoffice-core \
        libreoffice-draw \
        poppler-utils \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv>=0.6,<1" "Pillow>=10,<12"

WORKDIR /app

# Install application dependencies before copying the source so this layer can be cached.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY init ./init
COPY doc-audit-integrate-skill ./doc-audit-integrate-skill
COPY README.md ./
RUN uv sync --frozen --no-dev

# The bind-mounted runtime directory must be writable by this UID (see deploy/README.md).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin docguard \
    && mkdir -p /var/lib/docguard \
    && chown -R docguard:docguard /app /var/lib/docguard

USER docguard

EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "docguard.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
