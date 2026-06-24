APP_ID := nextcloud-document-tools
IMAGE ?= ghcr.io/kyasu-404/nextcloud-document-tools
TAG ?= 1.0.0

.PHONY: help build run-local

help:
	@echo "Targets:"
	@echo "  build      Build Docker image"
	@echo "  run-local  Run backend locally without AppAPI auth"

build:
	docker build -t $(IMAGE):$(TAG) .

run-local:
	cd ex_app/lib && DOCUMENT_TOOLS_DISABLE_APPAPI_AUTH=1 python main.py
