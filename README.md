# 🧠 Second Brain — Docker Edition

Local, private knowledge base with MCP tools for Claude Desktop. Optional **batch** zip backups into your iCloud Drive folder (one file per snapshot — not per-note sync).
No API keys. No cloud. Everything runs on your machine.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Docker Network                   │
│                                                     │
│  ┌──────────┐   ┌──────────┐   ┌─────────────────┐  │
│  │  ollama  │   │  chroma  │   │   mcp-server    │  │
│  │  :11434  │   │  :8000   │   │     :3777       │  │
│  └────┬─────┘   └────┬─────┘   └────────┬────────┘  │
│       │              │                  │           │
│  ┌────┴─────┐   ┌────┴─────┐            │           │
│  │ indexer  │   │ watcher  │   │ backup (opt.) │   │
│  │ (manual) │   │ (always) │   │ zip → iCloud  │   │
│  └──────────┘   └──────────┘   └───────────────┘   │
└─────────────────────────────────────────┼───────────┘
                                          │ SSE
                                   Claude Desktop
                                   (your machine)
```

---

## Prerequisites

- Docker Desktop ([Mac](https://www.docker.com/products/docker-desktop) or [Windows](https://www.docker.com/products/docker-desktop))
- Claude Desktop → [https://claude.ai/download](https://claude.ai/download)

---

## First-time Setup (new machine)

### 1. Get the project

If you already have it via iCloud, it's already on your machine. Otherwise:

```bash
# Copy or clone the second-brain/ folder anywhere you like, e.g.:
cd ~/Developer
# paste the second-brain/ folder here
```

### 2. Configure `.env`

Copy the template and edit paths (Docker Compose reads `.env` from the `second-brain/` folder on **Mac, Windows, and Linux**):

```bash
cp .env.example .env
```

Set **`VAULT_PATH`** to your Obsidian vault (folder that contains your `.md` notes and usually `.obsidian/`). Examples:

- **macOS (local disk, recommended):** `VAULT_PATH=/Users/you/Brain/second-brain-obsidian`
- **macOS (vault still inside iCloud Drive):** same idea, but the path lives under `Library/Mobile Documents/...` if you want Apple to sync raw notes.
- **Windows:** `VAULT_PATH=C:/Users/you/Brain/second-brain-obsidian` (forward slashes work well in `.env`)

Optional **batch backup** (zip snapshots into a folder — often your iCloud Drive directory on this machine): either rely on Compose defaults (`.brain-local/` under the repo, gitignored) or set `BRAIN_SNAPSHOT_LOCAL_DIR`, `BRAIN_ICLOUD_BACKUP_DIR`, and `BRAIN_STATE_DIR` in `.env`. Start with `make up-backup` or `.\win\brain.ps1 up-backup`. See [Batch backup (optional)](#batch-backup-optional).

**Git / remote repos:** The Obsidian vault is personal; it should not be pushed. A conventional folder name is `second-brain-obsidian` (listed in `.gitignore`). Secrets belong in `.env` (ignored); **commit `.env.example`, never `.env`.**

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

If you use **Git for Windows**, you can run the same `make` targets as on macOS (`Makefile.windows` simply includes `Makefile`).

### 5. Register the MCP server in Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

### 6. Optional: reach your local MCP from the cloud (Cloudflare Tunnel)

Claude Desktop on the same Mac can use `http://localhost:3777/sse` directly.
If you want **Claude web**, **Cursor**, or any other cloud client to use the same MCP, expose it via a tunnel — and enable OAuth so only you can access your vault.

See **[Security & Authentication](#security--authentication)** below for the full setup.

---

## Security & Authentication

### Local mode (default)

When `OAUTH_PASSWORD` and `JWT_SECRET` are **not** set, the server runs with no authentication.
This is safe for `http://localhost:3777` because only processes on your Mac can reach that port.

### Remote mode — OAuth 2.0 via Cloudflare Tunnel

When you expose the MCP over a public URL you must enable OAuth so only you can authorize access.
The server implements **OAuth 2.0 Authorization Code + PKCE** (RFC 6749 / RFC 7636), which is what Claude web, Cursor, and other MCP-compatible clients expect.

#### How the flow works

```
Claude web                         MCP server (your Mac)
    │                                      │
    │  GET /sse  →  401 + WWW-Authenticate │
    │◄─────────────────────────────────────│
    │                                      │
    │  GET /.well-known/oauth-*  (discover)│
    │─────────────────────────────────────►│
    │◄── OAuth metadata (endpoints, etc.) ─│
    │                                      │
    │  POST /oauth/register  (client reg.) │
    │─────────────────────────────────────►│
    │◄── client_id ────────────────────────│
    │                                      │
    │  Opens browser → GET /oauth/authorize│
    │─────────────────────────────────────►│
    │◄── Login page (password form) ───────│
    │                                      │
    │  POST /oauth/authorize  (password)   │
    │─────────────────────────────────────►│
    │◄── 302 → redirect_uri?code=…  ───────│
    │                                      │
    │  POST /oauth/token  (code + PKCE)    │
    │─────────────────────────────────────►│
    │◄── { access_token: "…JWT…" }  ───────│
    │                                      │
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

Cloudflare prints a public HTTPS URL like `https://xxx.trycloudflare.com`.
Copy it — you'll need it in the next step.

> For a stable URL instead of a random one, create a named tunnel in the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com) and connect it to your domain.

#### 2. Set environment variables

Add these to your `.env` file (copy `.env.example` as a starting point):

```bash
# The password shown on the login page in the browser
OAUTH_PASSWORD=your-strong-password-here

# Secret for signing JWT tokens — keep private, never commit
JWT_SECRET=$(openssl rand -hex 32)

# Your Cloudflare Tunnel public URL (no trailing slash)
PUBLIC_URL=https://xxx.trycloudflare.com

# How long tokens stay valid before re-login is required (default: 7 days)
JWT_EXPIRY_DAYS=7
```

Then rebuild and restart the MCP server:

```bash
make build-mcp
```

#### 3. Add to Claude web (claude.ai)

1. Open **claude.ai** → Settings → **Integrations** (or MCP)
2. Add a new MCP server with the URL:
   ```
   https://xxx.trycloudflare.com/sse
   ```
3. Claude will open a browser tab pointing to your login page
4. Enter your `OAUTH_PASSWORD`
5. Claude gets a token — your second brain is now available in every cloud conversation

The same URL works in **Cursor**, **OpenAI**, or any other client that supports remote MCP with OAuth.

#### Token lifecycle

| Event | Behaviour |
|---|---|
| First connection | Browser login, token issued for `JWT_EXPIRY_DAYS` days |
| Token valid | All requests pass through with no user interaction |
| Token expired | Client triggers a new login flow automatically |
| Container restart | Pending auth codes are cleared; issued tokens remain valid |

#### Security properties

- **PKCE (S256)** — prevents authorization code interception even if someone captures the redirect
- **JWT (HS256)** — tokens are signed with `JWT_SECRET`; tampered tokens are rejected
- **Timing-safe comparisons** — password and PKCE checks use `secrets.compare_digest`
- **Short-lived auth codes** — codes expire after 10 minutes and are single-use
- **No client secrets** — public clients (Claude web) are accepted; security comes from PKCE + password

> `.env` is in `.gitignore` — secrets are never committed. Rotate `JWT_SECRET` any time by updating `.env` and restarting (`make build-mcp`); all existing tokens are immediately invalidated.

---

## Batch backup (optional)

The **`backup`** service (Compose profile `backup`) keeps the **vault as the source of truth** on local disk and, on a schedule, builds a **single compressed zip** (`brain-full-YYYY-MM-DD-HH-MM.zip`) with a `manifest.json`, SHA256 sidecar, and SQLite state for retries and retention.

| What is backed up | What is *not* in the zip |
|-------------------|---------------------------|
| All files under `VAULT_PATH` (notes, `.obsidian/`, etc., minus optional `BACKUP_EXCLUDE_GLOBS`) | Chroma index, Ollama models (regenerate with `make index` after restore) |

**Flow:** staging copy → zip locally → checksum → copy zip + `.sha256` into `BRAIN_ICLOUD_BACKUP_DIR` → retention (never deletes the newest completed snapshot; skips rows still in error states you care about — failed copies stay until retry succeeds).

**Start with backup:**

```bash
make up-backup
# Windows: .\win\brain.ps1 up-backup
```

**One-off snapshot (same env as compose):**

```bash
make backup-once
```

**Restore from a zip:** extract the archive on the new machine, point `VAULT_PATH` at the extracted `vault/` tree (or merge into your vault), run `make up` and `make index` to rebuild embeddings.

**Limitations (v1):** full snapshots only; files can change while a snapshot runs (consistent enough for notes; heavy edits during the window may appear in the next run). Set `TZ` and `BACKUP_SNAPSHOT_TIME` for local wall-clock scheduling inside the container.

---

## Moving to another machine

Put the **vault** where you want it (local folder, or synced folder — your choice). Copy `.env` or recreate it with the same logical settings.

On the new machine:

1. Install Docker Desktop
2. Ensure `.env` exists with `VAULT_PATH` pointing at the vault on that machine
3. Run `make up` (or `.\win\brain.ps1 up`) from the `second-brain/` folder
4. Register the MCP server in Claude Desktop (same JSON config as in first-time setup)

Docker volumes (Chroma, Ollama) are **local** to that machine. After `make up`, run `make index` once so search works — or restore a **batch zip** into `VAULT_PATH` first, then `make index`.

---

## Daily usage

```bash
make up          # core stack
make up-backup   # core + backup service (override BRAIN_* in .env for custom / iCloud paths)
make down        # stop (data preserved)
make logs        # follow logs
make ps          # container status
```

---

## Makefile Commands

### Container lifecycle

```bash
make up
# Starts ollama, chroma, watcher, mcp-server. Does not run a full vault re-index.

make down
# Stops containers. DATA IS PRESERVED (volumes are kept).

make restart
# Restarts running containers without rebuilding.
```

### Rebuilding after code changes

Use these when you change code in mcp-server/ or watcher/ — they do NOT touch the database.

```bash
make build
# Rebuilds and restarts mcp-server + watcher only.

make build-mcp
# Rebuilds and restarts mcp-server only.

make build-watcher
# Rebuilds and restarts watcher only.

make build-backup
# Rebuilds and restarts the backup service (requires backup .env paths).
```

> **Note:** Chroma (the vector database) and Ollama are never touched by build commands.
> Your index is never deleted unless you explicitly run `docker compose down -v`.

### Vault & Indexing

```bash
make index
# Full vault pass into Chroma (use after first install, after down -v, or when you want a rebuild).
# Watcher handles incremental updates while the stack is up.

make search Q="your query"
# Semantic search across your vault, returns top 5 results.
# Example: make search Q="docker networking"
# Example: make search Q="black holes" TOP=3
```

### Chroma inspection

```bash
make check-chroma-databases
# Lists all collections in ChromaDB.

make check-chunks-by-collection
# Returns the total number of chunks in your second_brain collection.
# Example: make check-chunks-by-collection COLLECTION_ID=your-collection-id
```

### Batch backup

```bash
make backup-once
# One full zip + optional iCloud copy, then exit (Compose profile backup).

make backup-retry
# Retry failed iCloud copies recorded in SQLite.

make backup-retention
# Apply local + iCloud retention (keeps newest completed snapshot safe).
```

---

## WARNING: What deletes your index

```bash
docker compose down -v   # ← THIS deletes all data (Chroma + Ollama volumes)
```

**Never run `down -v` unless you want to start from scratch.**
`make down` is safe — it does NOT pass `-v`.

If you accidentally delete the index, run `make up` and then `make index` to rebuild it.

---

## Using in Claude Desktop

Once the MCP is registered, just talk to Claude naturally:

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


| Tool           | What it does                     |
| -------------- | -------------------------------- |
| `search_vault` | Semantic search across all notes |
| `get_note`     | Read a specific note by path     |
| `list_notes`   | List notes in a folder           |
| `list_tags`    | All tags in vault with counts    |
| `save_note`    | Create a new note from Claude    |


---

## Folder Structure

```
second-brain/
├── Makefile / Makefile.windows
├── win/brain.ps1 / win/brain.cmd   ← Windows helpers (no Make required)
├── docker-compose.yml
├── .env.example
├── backup-service/         ← optional batch zip + SQLite journal
├── indexer/
├── watcher/
└── mcp-server/
```

---

## Cost


| Service           | Cost               |
| ----------------- | ------------------ |
| Docker containers | Free               |
| Ollama embeddings | Free (local)       |
| ChromaDB          | Free (local)       |
| MCP server        | Free (local)       |
| Claude Desktop    | Your existing plan |


**Total additional cost: $0**