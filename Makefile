# Second Brain — Docker helpers (macOS, Linux, or Git Bash on Windows).
# Windows CMD without Make: use `win\brain.cmd` or `.\win\brain.ps1`.
# Compose reads `.env` from this directory automatically.

-include .env
export

guard-env:
	@test -f .env || (echo "ERROR: Copy .env.example to .env in the project root and set VAULT_PATH." && exit 1)
	@grep -qE '^[[:space:]]*VAULT_PATH=' .env || (echo "ERROR: VAULT_PATH missing in .env" && exit 1)

up: guard-env
	docker compose up -d

up-backup: guard-env
	docker compose --profile backup up -d

down:
	docker compose down

down-backup:
	docker compose --profile backup down

restart: guard-env
	docker compose restart

build: guard-env
	docker compose build --no-cache mcp-server watcher
	docker compose up -d --force-recreate mcp-server watcher

build-mcp: guard-env
	docker compose build --no-cache mcp-server
	docker compose up -d --force-recreate mcp-server

build-watcher: guard-env
	docker compose build --no-cache watcher
	docker compose up -d --force-recreate watcher

build-backup: guard-env
	docker compose build --no-cache backup
	docker compose up -d --force-recreate backup

logs:
	docker compose logs -f

ps:
	docker compose ps

index: guard-env
	docker compose --profile index run --rm indexer python3 indexer.py --full

backup-once: guard-env
	docker compose --profile backup run --rm backup python -m app snapshot-once

backup-retry: guard-env
	docker compose --profile backup run --rm backup python -m app retry

backup-retention: guard-env
	docker compose --profile backup run --rm backup python -m app retention

check-chroma-databases:
	curl "http://localhost:8000/api/v2/tenants/default_tenant/databases/default_database/collections"

COLLECTION_ID ?= 7bdc972d-1db6-40ce-aa1a-255e1218079c

check-chunks-by-collection:
	curl "http://localhost:8000/api/v2/tenants/default_tenant/databases/default_database/collections/$(COLLECTION_ID)/count"

# Usage: make search Q="docker networking"
Q ?=
TOP ?= 5
MCP_PORT ?= 3777

search:
	@test -n "$(Q)" || (echo "ERROR: Q is not set. Usage: make search Q=\"your query\"" && exit 1)
	curl -s -G "http://localhost:$(MCP_PORT)/search" --data-urlencode "q=$(Q)" -d "top_k=$(TOP)" | python3 -m json.tool
