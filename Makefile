SHELL := /bin/bash

VAULT_PATH ?= $(shell source ~/.zshrc 2>/dev/null && echo $$VAULT_PATH)

export VAULT_PATH

guard-vault:
	@test -n "$(VAULT_PATH)" || (echo "ERROR: VAULT_PATH is not set. Add it to ~/.zshrc: export VAULT_PATH=/path/to/vault" && exit 1)
	@echo "VAULT_PATH=$(VAULT_PATH)"

up: guard-vault
	docker-compose up -d

down:
	docker-compose down

restart: guard-vault
	docker-compose restart

build: guard-vault
	docker-compose build --no-cache mcp-server watcher
	docker-compose up -d --force-recreate mcp-server watcher

build-mcp: guard-vault
	docker-compose build --no-cache mcp-server
	docker-compose up -d --force-recreate mcp-server

build-watcher: guard-vault
	docker-compose build --no-cache watcher
	docker-compose up -d --force-recreate watcher

logs:
	docker-compose logs -f

ps:
	docker-compose ps

index:
	docker-compose run --rm indexer python3 indexer.py --full

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