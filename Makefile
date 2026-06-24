APP_ID := document_tools
IMAGE ?= ghcr.io/example/nextcloud-document-tools
TAG ?= latest

.PHONY: help build run-local

help:
	@echo "Targets:"
	@echo "  build      Build Docker image"
	@echo "  run-local  Run backend locally without AppAPI auth"

build:
	docker build -t $(IMAGE):$(TAG) .

run-local:
	cd ex_app/lib && DOCUMENT_TOOLS_DISABLE_APPAPI_AUTH=1 python main.py
