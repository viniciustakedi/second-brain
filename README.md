# 🧠 Second Brain — Docker Edition

Local, private knowledge base with MCP tools for Claude. Optional **batch** zip backups into your iCloud Drive folder (one file per snapshot — not per-note sync).
No API keys. No cloud. Everything runs on your machine.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Docker Network                        │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────────────┐  │
│  │  ollama  │   │  chroma  │   │       mcp-server         │  │
│  │  :11434  │   │  :8000   │   │         :3777            │  │
│  └────┬─────┘   └────┬─────┘   └────────────┬─────────────┘  │
│       │              │                      │                │
│  ┌────┴─────┐   ┌────┴─────┐   ┌────────────┴─────────────┐  │
│  │ indexer  │   │ watcher  │   │     backup-service       │  │
│  │ (manual) │   │ (always) │   │  SQLite + zip → iCloud   │  │
│  └──────────┘   └──────────┘   └──────────────────────────┘  │
└────────────────────────────────────────────┬─────────────────┘
                                             │ SSE
                              ┌──────────────┴───────────────┐
                              │  Claude Desktop / Claude Code │
                              │  Claude web (via tunnel)      │
                              └──────────────────────────────┘
```

---

## Prerequisites

- Docker Desktop ([Mac](https://www.docker.com/products/docker-desktop) or [Windows](https://www.docker.com/products/docker-desktop))
- Claude Desktop → [https://claude.ai/download](https://claude.ai/download) **or** Claude Code CLI

---

## First-time Setup (new machine)

### 1. Get the project

If you already have it via iCloud, it's already on your machine. Otherwise copy or clone the `second-brain/` folder anywhere you like.

### 2. Configure `.env`

Copy the template and fill in your paths (Docker Compose reads `.env` from the `second-brain/` folder on Mac, Windows, and Linux):

```bash
cp .env.example .env
```

**`VAULT_PATH`** (required) — the folder containing your `.md` notes:

| Platform | Example |
|---|---|
| macOS (local Documents) | `VAULT_PATH=/Users/you/Documents/second-brain/memories` |
| macOS (iCloud Drive vault) | `VAULT_PATH=/Users/you/Library/Mobile Documents/com~apple~CloudDocs/second-brain/memories` |
| Windows | `VAULT_PATH=C:/Users/you/Documents/second-brain/memories` |

**Recommended Mac layout** (vault local, backups to iCloud):

```
~/Documents/second-brain/
├── memories/          ← VAULT_PATH (.md notes, .obsidian/)
├── snapshots/         ← BRAIN_SNAPSHOT_LOCAL_DIR (local zip archives)
└── snapshots/state/   ← BRAIN_STATE_DIR (SQLite journal)

~/Library/Mobile Documents/com~apple~CloudDocs/second-brain/backup/
                       ← BRAIN_ICLOUD_BACKUP_DIR (iCloud drop folder)
```

**Batch backup** variables (only needed for `make up-backup`):

```bash
BRAIN_SNAPSHOT_LOCAL_DIR=/Users/you/Documents/second-brain/snapshots
BRAIN_ICLOUD_BACKUP_DIR=/Users/you/Library/Mobile Documents/com~apple~CloudDocs/second-brain/backup
BRAIN_STATE_DIR=/Users/you/Documents/second-brain/snapshots/state

BACKUP_SNAPSHOT_TIME=23:59          # wall-clock time for daily snapshot (uses TZ below)
BACKUP_RETENTION_LOCAL_DAYS=14
BACKUP_RETENTION_ICLOUD_DAYS=30
TZ=America/Sao_Paulo                # IANA timezone for the scheduler
```

If these are not set, the Compose defaults to `.brain-local/` under the repo (gitignored) so the stack starts without them.

> `.env` is gitignored — secrets are never committed. Commit `.env.example`, never `.env`.

### 3. Start everything

```bash
make up
```

First run pulls Docker images and the embedding model — takes ~5 min.
After that, `make up` starts the stack without a full re-index (Chroma keeps your vectors across restarts).

**First time** (empty Chroma) or after `docker compose down -v`, build the index once:

```bash
make index
```

Day-to-day changes are picked up by the **watcher** while the stack is running.

### 4. Windows without Make

From the project folder:

- **PowerShell:** `.\win\brain.ps1 up` — see `.\win\brain.ps1 help`
- **CMD:** `win\brain.cmd up`

If you use **Git for Windows / Git Bash**, you can run the same `make` targets as on macOS.

### 5. Register the MCP server

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "second-brain": {
      "type": "sse",
      "url": "http://localhost:3777/sse"
    }
  }
}
```

Fully quit and reopen Claude Desktop. You'll see a 🔧 tools icon — your second brain is live.

**Claude Code** — add to `~/.mcp.json` (global, works across all projects):

```json
{
  "mcpServers": {
    "second-brain": {
      "type": "sse",
      "url": "http://localhost:3777/sse"
    }
  }
}
```

Restart Claude Code after saving.

### 6. Optional: reach your local MCP from the cloud (tunnel + OAuth)

Claude Desktop and Claude Code on the same machine can use `http://localhost:3777/sse` directly.
If you want **Claude web**, **Cursor**, or any other cloud client to use the same MCP, expose it via a tunnel and enable OAuth so only you can access your vault.

See **[Security & Authentication](#security--authentication)** below for the full setup.

---

## Security & Authentication

### Local mode (default)

When `OAUTH_PASSWORD` and `JWT_SECRET` are **not** set, the server runs with no authentication.
This is safe for `http://localhost:3777` because only processes on your machine can reach that port.

### Remote mode — OAuth 2.0 via Cloudflare Tunnel

When you expose the MCP over a public URL you must enable OAuth so only you can authorize access.
The server implements **OAuth 2.0 Authorization Code + PKCE** (RFC 6749 / RFC 7636), which is what Claude web, Cursor, and other MCP-compatible clients expect.

#### How the flow works

```
Claude web                         MCP server (your Mac)
    │                                      │
    │  GET /sse  →  401 + WWW-Authenticate │
    │◄─────────────────────────────────────│
    │  GET /.well-known/oauth-*  (discover)│
    │─────────────────────────────────────►│
    │◄── OAuth metadata ───────────────────│
    │  POST /oauth/register                │
    │─────────────────────────────────────►│
    │◄── client_id ────────────────────────│
    │  Opens browser → GET /oauth/authorize│
    │─────────────────────────────────────►│
    │◄── Login page (password form) ───────│
    │  POST /oauth/authorize  (password)   │
    │─────────────────────────────────────►│
    │◄── 302 → redirect_uri?code=… ────────│
    │  POST /oauth/token  (code + PKCE)    │
    │─────────────────────────────────────►│
    │◄── { access_token: "…JWT…" } ────────│
    │  GET /sse  →  Authorization: Bearer  │
    │─────────────────────────────────────►│
    │◄── MCP stream ────────────────────────│
```

#### 1. Install and start Cloudflare Tunnel

```bash
# macOS
brew install cloudflared

# Start a tunnel to the MCP port (run while `make up` is running)
cloudflared tunnel --url http://localhost:3777
```

Cloudflare prints a public HTTPS URL like `https://xxx.trycloudflare.com`. Copy it.

> For a stable URL, create a named tunnel in the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com) and connect it to your domain.

#### 2. Set environment variables

```bash
OAUTH_PASSWORD=your-strong-password-here
JWT_SECRET=$(openssl rand -hex 32)
PUBLIC_URL=https://xxx.trycloudflare.com
JWT_EXPIRY_DAYS=7
```

Then rebuild the MCP server:

```bash
make build-mcp
```

#### 3. Add to Claude web (claude.ai)

1. Open **claude.ai** → Settings → **Integrations**
2. Add MCP server URL: `https://xxx.trycloudflare.com/sse`
3. Claude opens a browser tab → enter your `OAUTH_PASSWORD`
4. Claude gets a token — your second brain is live in every cloud conversation

The same URL works in **Cursor**, **OpenAI**, or any client that supports remote MCP with OAuth.

#### Token lifecycle

| Event | Behaviour |
|---|---|
| First connection | Browser login, token issued for `JWT_EXPIRY_DAYS` days |
| Token valid | All requests pass through with no user interaction |
| Token expired | Client triggers a new login flow automatically |
| Container restart | Pending auth codes cleared; issued tokens remain valid |

#### Security properties

- **PKCE (S256)** — prevents authorization code interception
- **JWT (HS256)** — tokens signed with `JWT_SECRET`; tampered tokens rejected
- **Timing-safe comparisons** — password and PKCE checks use `secrets.compare_digest`
- **Short-lived auth codes** — codes expire after 10 minutes and are single-use

> Rotate `JWT_SECRET` any time by updating `.env` and running `make build-mcp` — all existing tokens are immediately invalidated.

---

## Batch backup (optional)

The **`backup-service`** (Compose profile `backup`) keeps the vault as the source of truth and, on a daily schedule, produces a compressed zip snapshot that is copied to your iCloud drop folder.

### What's inside a snapshot

| Included | Not included (regenerable) |
|---|---|
| All vault files (`.md`, `.obsidian/`, etc.) | Chroma index — rebuild with `make index` |
| Optional exclusions via `BACKUP_EXCLUDE_GLOBS` | Ollama models — re-pulled automatically |

### How it works

1. **FS watcher** records `created / modified / deleted / moved` events in SQLite (`fs_events` table) — used for audit and future incremental backups.
2. **Daily scheduler** at `BACKUP_SNAPSHOT_TIME` (wall-clock, respects `TZ`):
   - Copies vault to a staging area
   - Zips into `brain-full-YYYY-MM-DD-HH-MM.zip` + `manifest.json` + `.zip.sha256` sidecar
   - Atomically copies zip to `BRAIN_ICLOUD_BACKUP_DIR` (`.part` + rename)
   - Records result in SQLite (`snapshots` table) with status, checksum, path, attempts
3. **Retry job** (every `BACKUP_RETRY_COPY_INTERVAL_SEC`) retries failed iCloud copies.
4. **Retention** removes old archives locally and from iCloud, never deleting the newest completed snapshot.

### Start with backup

```bash
make up-backup
# Windows: .\win\brain.ps1 up-backup
```

### One-off snapshot

```bash
make backup-once
```

### Restore from a zip

1. Copy `brain-full-….zip` from iCloud to the target machine
2. Verify checksum: `sha256sum -c brain-full-….zip.sha256`
3. Extract — the archive contains the full vault tree
4. Point `VAULT_PATH` at the extracted folder
5. Run `make up` then `make index` to rebuild embeddings

**Limitation (v1):** full snapshots only. Files changed while a snapshot runs appear in the next run.

---

## Moving to another machine

1. Install Docker Desktop
2. Copy `.env` or recreate it with `VAULT_PATH` pointing at the vault on the new machine
3. Run `make up` (or `.\win\brain.ps1 up`)
4. Register the MCP server (same JSON config as in first-time setup)
5. Run `make index` once to build the vector index — or restore from a backup zip first

Docker volumes (Chroma, Ollama) are local to each machine and rebuilt from scratch.

---

## Daily usage

```bash
make up          # start core stack
make up-backup   # start core + backup service
make down        # stop (data preserved)
make logs        # follow logs
make ps          # container status
```

---

## Makefile Commands

### Container lifecycle

```bash
make up           # ollama, chroma, watcher, mcp-server
make down         # stop containers — DATA IS PRESERVED (volumes kept)
make restart      # restart running containers without rebuilding
make up-backup    # core stack + backup service
make down-backup  # stop backup service only
```

### Rebuild after code changes

These do NOT touch Chroma or Ollama volumes.

```bash
make build          # rebuild mcp-server + watcher
make build-mcp      # rebuild mcp-server only
make build-watcher  # rebuild watcher only
make build-backup   # rebuild backup service only
make build-indexer  # rebuild indexer only — required after editing indexer/indexer.py
```

> **Important:** `make index` runs the pre-built indexer image. If you edit `indexer/indexer.py`, run `make build-indexer` first — otherwise the old image is used and your changes have no effect.

### Vault & indexing

```bash
make index              # full vault re-index into Chroma
make search Q="query"   # semantic search, returns top 5 results
                        # e.g.: make search Q="docker networking"
                        # e.g.: make search Q="black holes" TOP=3
```

### Chroma inspection

```bash
make check-chroma-databases       # list all collections in ChromaDB
make check-chunks-by-collection   # total chunks in second_brain collection
```

### Backup

```bash
make backup-once       # one full snapshot + iCloud copy, then exit
make backup-retry      # retry failed iCloud copies recorded in SQLite
make backup-retention  # apply local + iCloud retention policy
```

---

## WARNING: What deletes your index

```bash
docker compose down -v   # ← THIS deletes all data (Chroma + Ollama volumes)
```

**Never run `down -v` unless you want to start from scratch.**
`make down` is safe — it does NOT pass `-v`.

If you accidentally delete the index: `make up` then `make index`.

---

## Using with Claude

Once the MCP is registered, talk to Claude naturally:

```
You: "What do I know about Docker networking?"
Claude: [calls search_vault] → finds your notes → answers

You: "List all my tags"
Claude: [calls list_tags] → shows all tags with counts

You: "Read my note about microservices"
Claude: [calls get_note] → reads the full note content

You: "List all notes in my coding folder"
Claude: [calls list_notes] → shows all notes in that folder

You: "Save a note about what we just discussed"
Claude: [calls save_note] → saves directly to your vault
```

---

## MCP Tools Available

| Tool | What it does |
|---|---|
| `search_vault` | Semantic search across all notes (supports `top_k` param, default 3) |
| `get_note` | Read a specific note by relative path |
| `list_notes` | List notes in a folder |
| `list_tags` | All tags in vault with counts |
| `save_note` | Create a new note from Claude directly into the vault |

---

## Folder Structure

```
second-brain/
├── Makefile                    ← Mac / Linux / Git Bash on Windows
├── Makefile.windows            ← includes Makefile (GNU Make on Windows)
├── docker-compose.yml
├── .env.example
├── win/
│   ├── brain.ps1               ← PowerShell helper (no Make required)
│   └── brain.cmd               ← CMD helper
├── backup-service/             ← SQLite journal + zip snapshots + iCloud copy
│   └── app/
│       ├── config.py
│       ├── db.py               ← SQLite schema (fs_events, snapshots)
│       ├── fs_watcher.py       ← vault file watcher → SQLite
│       ├── snapshot_job.py     ← staging copy → zip → iCloud
│       ├── retention.py        ← local + iCloud retention policy
│       └── __main__.py         ← CLI entry point
├── indexer/                    ← one-shot vault indexer into Chroma
├── watcher/                    ← continuous watcher → re-indexes on change
└── mcp-server/                 ← MCP SSE server + OAuth 2.0
```

---

## Cost

| Service | Cost |
|---|---|
| Docker containers | Free |
| Ollama embeddings | Free (local) |
| ChromaDB | Free (local) |
| MCP server | Free (local) |
| Claude Desktop / Claude Code | Your existing plan |

**Total additional cost: $0**
