# slopstop build entries (BILL-18).
#
# User-facing make targets that wrap the docker workflow for the ticket-rag
# service container. Documented in docker/postgres-pgvector/README.md.
#
# Targets:
#   rag-build       — build the image with both :<git-sha> and :latest tags
#   rag-run         — build (if needed) and run the BILL-17 end-to-end smoke test
#   rag-clean       — remove the slopstop-rag images and any leftover
#                     smoke-test container
#   rag-clean-deep  — rag-clean plus a full BuildKit cache prune. Use when
#                     Docker Desktop VM disk pressure accumulates from
#                     repeated builds.
#
#   rag-dev-start   — start a persistent dev container using pgdata/ for
#                     stable storage; port 7777 published to localhost.
#                     Sources .harvester.toml for LINEAR_API_KEY if present.
#   rag-dev-stop    — stop and remove the dev container (data stays in pgdata/).
#   rag-dev-status  — show whether the dev container is running.
#
# Run from the repo root.

DOCKER_DIR    := docker/postgres-pgvector
IMAGE_NAME    := slopstop-rag
GIT_SHA       := $(shell git rev-parse --short HEAD)
DEV_CONTAINER := slopstop-rag-dev
DEV_PORT      := 7777

.PHONY: rag-build rag-run rag-clean rag-clean-deep rag-dev-start rag-dev-stop rag-dev-status

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

# Start a persistent dev container.  pgdata/ survives stop/start so the
# indexed tickets accumulate across sessions.  Port $(DEV_PORT) is published
# so host-side tools (search.sh, curl) can hit the service directly.
# LINEAR_API_KEY is passed through when present so in-container harvester
# invocations work without re-sourcing credentials.
rag-dev-start: rag-build
	@if docker ps -q --filter "name=^$(DEV_CONTAINER)$$" | grep -q .; then \
	    echo "$(DEV_CONTAINER) already running"; \
	else \
	    bash -c '. ./.harvester.toml 2>/dev/null || true; \
	        docker run -d \
	            --name $(DEV_CONTAINER) \
	            -v "$(CURDIR)/pgdata:/var/lib/postgresql" \
	            -e APP_HOST=0.0.0.0 \
	            -p $(DEV_PORT):$(DEV_PORT) \
	            $${LINEAR_API_KEY:+-e "LINEAR_API_KEY=$$LINEAR_API_KEY"} \
	            $(IMAGE_NAME):latest && \
	        echo "$(DEV_CONTAINER) started — http://localhost:$(DEV_PORT)/healthz"'; \
	fi

rag-dev-stop:
	docker stop $(DEV_CONTAINER) 2>/dev/null || true
	docker rm   $(DEV_CONTAINER) 2>/dev/null || true

rag-dev-status:
	@docker ps --filter "name=^$(DEV_CONTAINER)$$" \
	    --format "running  ports={{.Ports}}" | grep . || echo "not running"
