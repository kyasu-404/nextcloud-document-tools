# Nextcloud Document Tools

Python ExApp for Nextcloud AppAPI that provides document conversion and OCR tools.

## Features

- PDF -> DOCX with `pdf2docx`
- DOCX -> PDF with LibreOffice headless
- PDF -> searchable PDF with PaddleOCR and PyMuPDF, with OCRmyPDF/Tesseract fallback
- PDF -> TXT with PyMuPDF, with OCR fallback for scanned PDFs
- DOCX -> Markdown with Mammoth, with Pandoc fallback
- HTML -> PDF with WeasyPrint, with LibreOffice fallback
- EPUB -> PDF with Calibre `ebook-convert`, with Pandoc fallback
- Image OCR to TXT or searchable PDF with PaddleOCR, with Tesseract fallback
- Top menu page in Nextcloud
- Files context menu action: `Конвертировать документ`
- In-memory processing queue with downloadable results and save-back to Nextcloud Files

## AppAPI and HaRP notes

This app is structured as a Nextcloud ExApp:

- `appinfo/info.xml` declares the Docker image and proxy routes.
- `ex_app/lib/main.py` implements FastAPI endpoints and AppAPI lifecycle handlers.
- `enabled_handler()` registers:
  - top menu entry `Nextcloud Document Tools`
  - top menu script and style resources
  - file action menu item `Конвертировать документ`
- The Docker image includes HaRP FRP support by using the official HaRP `start.sh` and bundled `frpc`.

References:

- ExApp overview: https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/development_overview/ExAppOverview.html
- ExApp development flow: https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/development_overview/ExAppDevelopmentSteps.html
- HaRP adaptation for ExApps: https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/development_overview/ExAppHarpIntegration.html
- AppAPI / External Apps admin docs: https://docs.nextcloud.com/server/latest/admin_manual/exapps_management/AppAPIAndExternalApps.html
- HaRP repository: https://github.com/nextcloud/HaRP

## Build

Update the image name in `appinfo/info.xml` before publishing, then build:

```bash
docker build -t ghcr.io/kyasu-404/nextcloud-document-tools:1.0.0 .
```

Sanity-check the HaRP entrypoint before pushing:

```bash
docker run --rm --entrypoint /bin/sh \
  ghcr.io/kyasu-404/nextcloud-document-tools:1.0.0 \
  -c 'ls -l /start.sh /usr/local/bin/frpc /app/ex_app/lib/main.py && /usr/local/bin/frpc --version'
```

Push the image before registering the ExApp through AppAPI:

```bash
docker push ghcr.io/kyasu-404/nextcloud-document-tools:1.0.0
docker manifest inspect ghcr.io/kyasu-404/nextcloud-document-tools:1.0.0
```

If the GHCR package is private, either make it public or make sure the Docker
daemon used by HaRP can pull it. A failed pull is surfaced by AppAPI as an
`images/create` error from the HaRP Docker proxy.

The image intentionally contains LibreOffice, PaddleOCR, PyMuPDF, OCRmyPDF, Tesseract, Pandoc, Calibre, and WeasyPrint dependencies. It will be large.

PaddleOCR downloads language models on first OCR use into `/root/.paddleocr`.
The running ExApp container reuses that cache, but fully offline deployments should
preload the required `en`/`ru` models during image build.

If AppAPI reports `container startup failed`, check the ExApp container status:

```bash
docker ps --filter name=nc_app_document_tools
docker logs nc_app_document_tools
```

When HaRP is used, `nc_py_api` runs Uvicorn on `/tmp/exapp.sock`. The Docker
healthcheck therefore checks that Unix socket first. A stale image with a
TCP-only healthcheck can stay in `health: starting` even though the app logs say
`Application startup complete`.

Runtime diagnostics are available from the ExApp proxy:

```text
/api/diagnostics
```

It reports Python imports, required system commands, installed Tesseract
languages, and writable storage status.

Do not put literal `%` into AppAPI notification rich subject/message strings
unless it is escaped as `%%` or supplied through rich object params. Nextcloud's
notification renderer localizes those strings and unescaped percent markers can
break the Notifications endpoint for the user.

## Register on Nextcloud

Copy `appinfo/info.xml` into the Nextcloud custom app directory and register the
same app id that is declared in `info.xml`:

```bash
sudo mkdir -p /opt/nextcloud/html/custom_apps/nextcloud-document-tools/appinfo/

docker cp appinfo/info.xml \
  nextcloud:/var/www/html/custom_apps/nextcloud-document-tools/appinfo/info.xml

docker exec -u root nextcloud \
  chown -R www-data:www-data \
  /var/www/html/custom_apps/nextcloud-document-tools

docker exec -u www-data nextcloud php occ \
  app_api:app:register \
  nextcloud-document-tools \
  harp_proxy_docker \
  --info-xml=/var/www/html/custom_apps/nextcloud-document-tools/appinfo/info.xml \
  --wait-finish
```

## HaRP deployment sketch

For a custom Docker setup, install AppAPI in Nextcloud, run HaRP, register it in:

`Settings -> Administration -> AppAPI -> Register Daemon -> HaRP Proxy (Docker)`

Typical HaRP container:

```bash
docker run \
  -e HP_SHARED_KEY="replace_with_a_long_secret" \
  -e NC_INSTANCE_URL="https://nextcloud.example.com" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)/certs:/certs" \
  --name appapi-harp \
  --restart unless-stopped \
  -p 8780:8780 \
  -p 8782:8782 \
  -d ghcr.io/nextcloud/nextcloud-appapi-harp:release
```

Required traffic for HaRP:

- Nextcloud -> HaRP on `8780` or `8781`
- HaRP -> Nextcloud using `NC_INSTANCE_URL`
- ExApp -> HaRP on `8782`
- ExApp -> Nextcloud using the daemon's Nextcloud URL

## Local backend run

Local mode skips AppAPI authentication and is useful only for API/UI development:

```bash
pip install -r requirements.txt
cd ex_app/lib
DOCUMENT_TOOLS_DISABLE_APPAPI_AUTH=1 python main.py
```

Then open `http://127.0.0.1:23000/js/document_tools-main.js` only to verify static serving. The real UI is mounted by Nextcloud AppAPI as a top menu embedded page.

## Save-back behavior

Completed jobs can be downloaded or saved back into Nextcloud Files:

- `Save back to Nextcloud` stores the result next to the original Nextcloud file, or in the Files root for local uploads.
- `Save to folder` opens the Nextcloud folder picker and stores the result in the selected folder.
- `Replace original file` is limited to compatible replacements, for example PDF -> searchable PDF, so a DOCX is not silently replaced with PDF bytes under a `.docx` name.

## Suggested next improvements

- Persist job state as JSON in `APP_PERSISTENT_STORAGE`.
- Add per-user queue isolation if several users run conversions concurrently.
- Add GPU Docker tags for PaddleOCR (`:cuda` / `:rocm`) if the HaRP daemon exposes a compute device.
- Add file size and runtime limits in settings to protect the ExApp host.
