# Nextcloud Document Tools

Python ExApp for Nextcloud AppAPI that provides document conversion and OCR tools.

## Features

- PDF -> DOCX with `pdf2docx`
- DOCX -> PDF with LibreOffice headless
- PDF -> searchable PDF with PaddleOCR and PyMuPDF
- PDF -> TXT with PyMuPDF, with OCR fallback for scanned PDFs
- DOCX -> Markdown with Mammoth, with Pandoc fallback
- HTML -> PDF with WeasyPrint, with LibreOffice fallback
- EPUB -> PDF with Calibre `ebook-convert`, with Pandoc fallback
- Image OCR to TXT or searchable PDF with PaddleOCR
- Top menu page in Nextcloud
- Files context menu action: `Конвертировать документ`
- In-memory processing queue with downloadable results

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

The image intentionally contains LibreOffice, PaddleOCR, PyMuPDF, Pandoc, Calibre, and WeasyPrint dependencies. It will be large.

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

## Current limitation

`Download` works for completed jobs. `Save back to Nextcloud`, `Replace original file`, and `Save to folder` are present in the UI and API surface, but return `501` until tested against a live Nextcloud AppAPI/WebDAV context. This is deliberate: writing into user files should be finished against a real server to verify permissions, shares, conflict handling, and path resolution.

## Suggested next improvements

- Persist job state as JSON in `APP_PERSISTENT_STORAGE`.
- Add authenticated WebDAV save-back support using `nc_py_api` once a test Nextcloud is available.
- Add per-user queue isolation if several users run conversions concurrently.
- Add GPU Docker tags for PaddleOCR (`:cuda` / `:rocm`) if the HaRP daemon exposes a compute device.
- Add file size and runtime limits in settings to protect the ExApp host.
