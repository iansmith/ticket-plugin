# ticket-plugin build entries (BILL-18).
#
# User-facing make targets that wrap the docker workflow for the ticket-rag
# service container. Documented in docker/postgres-pgvector/README.md.
#
# Targets:
#   rag-build       — build the image with both :<git-sha> and :latest tags
#   rag-run         — build (if needed) and run the BILL-17 end-to-end smoke test
#   rag-clean       — remove the ticket-plugin/rag images and any leftover
#                     smoke-test container
#   rag-clean-deep  — rag-clean plus a full BuildKit cache prune. Use when
#                     Docker Desktop VM disk pressure accumulates from
#                     repeated builds.
#
# Run from the repo root.

DOCKER_DIR := docker/postgres-pgvector
IMAGE_NAME := ticket-plugin/rag
GIT_SHA    := $(shell git rev-parse --short HEAD)

.PHONY: rag-build rag-run rag-clean rag-clean-deep

# Build context is the repo root (not $(DOCKER_DIR)/) so the Dockerfile can
# COPY from rag-service/ alongside the docker/ assets. BILL-29 relocated the
# application code from docker/postgres-pgvector/app/ to rag-service/; the
# Dockerfile path stays at $(DOCKER_DIR)/Dockerfile. A repo-root .dockerignore
# keeps the build context small.
rag-build:
	docker build \
		-f $(DOCKER_DIR)/Dockerfile \
		-t $(IMAGE_NAME):$(GIT_SHA) \
		-t $(IMAGE_NAME):latest \
		.

rag-run: rag-build
	bash $(DOCKER_DIR)/verify-bill17.sh $(IMAGE_NAME):latest

rag-clean:
	-docker rm -f ticket-rag-bill17-verify 2>/dev/null
	-IMG_IDS=$$(docker images -q $(IMAGE_NAME) 2>/dev/null); \
	  if [ -n "$$IMG_IDS" ]; then docker rmi -f $$IMG_IDS; fi

rag-clean-deep: rag-clean
	docker builder prune -a -f
