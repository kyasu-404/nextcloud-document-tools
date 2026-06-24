FROM ghcr.io/nextcloud/nextcloud-appapi-harp:release AS harp

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCUMENT_TOOLS_OCR_LANG=rus+eng \
    DOCUMENT_TOOLS_MAX_WORKERS=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=23000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-dejavu \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        libreoffice \
        libreoffice-writer \
        pandoc \
        calibre \
        poppler-utils \
        ocrmypdf \
        qpdf \
        ghostscript \
        file \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# HaRP compatibility: start.sh launches FRP client when AppAPI provides HaRP
# variables, then execs the application command below. Copy frpc from the
# official HaRP image instead of downloading it during build.
COPY docker/start.sh /start.sh
COPY --from=harp /usr/bin/frpc /usr/local/bin/frpc
RUN chmod +x /start.sh /usr/local/bin/frpc

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY appinfo /app/appinfo
COPY ex_app /app/ex_app
COPY healthcheck.sh /app/healthcheck.sh
RUN chmod +x /app/healthcheck.sh

EXPOSE 23000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD /app/healthcheck.sh

ENTRYPOINT ["/start.sh"]
CMD ["python", "-u", "/app/ex_app/lib/main.py"]
