FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCUMENT_TOOLS_OCR_LANG=ru \
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
        tesseract-ocr \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# HaRP compatibility: start.sh launches FRP client when AppAPI provides HaRP
# variables, then execs the application command below.
RUN curl -fsSL https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/start.sh -o /start.sh \
    && chmod +x /start.sh \
    && set -ex; \
        ARCH="$(uname -m)"; \
        if [ "$ARCH" = "aarch64" ]; then \
            FRP_URL="https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/frp_0.61.1_linux_arm64.tar.gz"; \
        else \
            FRP_URL="https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/frp_0.61.1_linux_amd64.tar.gz"; \
        fi; \
        curl -fsSL "$FRP_URL" -o /tmp/frp.tar.gz; \
        tar -C /tmp -xzf /tmp/frp.tar.gz; \
        mv /tmp/frp_0.61.1_linux_* /tmp/frp; \
        cp /tmp/frp/frpc /usr/local/bin/frpc; \
        chmod +x /usr/local/bin/frpc; \
        rm -rf /tmp/frp /tmp/frp.tar.gz

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
