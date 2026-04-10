# Second Brain — Docker helpers (macOS, Linux, or native Make on Windows using cmd.exe).
# Prefer PowerShell if Make annoys you: .\win\brain.ps1 up
# Compose reads `.env` from this directory automatically.

-include .env
export

# Windows (e.g. Chocolatey GnuWin32 make, or MSVC nmake users: use win\brain.cmd instead).
# On Windows, GNU Make often runs recipes with cmd.exe — no `test` or `grep`.
ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /c

guard-env:
	@if not exist .env (echo ERROR: Copy .env.example to .env in the project root and set VAULT_PATH. & exit /b 1)
	@findstr /R /C:"VAULT_PATH=" .env >nul 2>&1 || (echo ERROR: VAULT_PATH missing in .env & exit /b 1)

else

guard-env:
	@test -f .env || (echo "ERROR: Copy .env.example to .env in the project root and set VAULT_PATH." && exit 1)
	@grep -qE '^[[:space:]]*VAULT_PATH=' .env || (echo "ERROR: VAULT_PATH missing in .env" && exit 1)

endif

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

build-indexer: guard-env
	docker compose --profile index build --no-cache indexer

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

ifeq ($(OS),Windows_NT)
search:
	@if "$(Q)"=="" (echo ERROR: Q is not set. Usage: make search Q=your-query & exit /b 1)
	curl -s -G "http://localhost:$(MCP_PORT)/search" --data-urlencode "q=$(Q)" -d "top_k=$(TOP)" | python -m json.tool
else
search:
	@test -n "$(Q)" || (echo "ERROR: Q is not set. Usage: make search Q=\"your query\"" && exit 1)
	curl -s -G "http://localhost:$(MCP_PORT)/search" --data-urlencode "q=$(Q)" -d "top_k=$(TOP)" | python3 -m json.tool
endif
