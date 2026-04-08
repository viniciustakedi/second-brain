# 🧠 Second Brain — Docker Edition

Local, private, iCloud-synced knowledge base with MCP tools for Claude Desktop.
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
│  │ indexer  │   │ watcher  │            │           │
│  │ (once)   │   │ (always) │            │           │
│  └──────────┘   └──────────┘            │           │
└─────────────────────────────────────────┼───────────┘
                                          │ SSE
                                   Claude Desktop
                                   (your machine)
```

---

## Prerequisites

- Docker Desktop for Mac → [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
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

### 2. Set your vault path

Add this to your `~/.zshrc` (adjust path to where your Obsidian vault is):

```bash
export VAULT_PATH=/Users/yourname/Library/Mobile\ Documents/com~apple~CloudDocs/second-brain/second-brain-obsidian
```

Reload:

```bash
source ~/.zshrc
```

The Makefile reads `VAULT_PATH` from your environment automatically — no `.env` file needed.

**Git / remote repos:** The Obsidian vault (your `.md` files and `.obsidian/`) is personal; it should not be pushed. This project assumes the vault lives in a sibling folder named `second-brain-obsidian` (“Second Brain Obsidian”). That path is already listed in `.gitignore`. If you use another folder name, add it to `.gitignore` at the repo root before committing. Secrets belong in `.env` (ignored); copy from `.env.example` for the template only — **commit `.env.example`, never `.env`.**

### 3. Start everything

```bash
make up
```

First run pulls Docker images and the embedding model — takes ~5 min.
After that, startup is instant. Your vault is indexed automatically on first run.

### 4. Register the MCP server in Claude Desktop

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

---

## Moving to another machine

Your vault (notes) lives in iCloud — already synced.
The Docker project folder can also be in iCloud — also synced.

On the new machine:

1. Make sure Docker Desktop is installed
2. Add `VAULT_PATH` to `~/.zshrc` and run `source ~/.zshrc`
3. Run `make up` from inside the `second-brain/` folder
4. Register the MCP server in Claude Desktop (step 4 above)

The Chroma index is rebuilt automatically on the new machine. No manual steps needed.

---

## Daily usage

```bash
make up       # start everything
make down     # stop everything (data is preserved)
make logs     # watch live logs
make ps       # check container status
```

---

## Makefile Commands

### Container lifecycle

```bash
make up
# Starts all containers. Safe to run anytime — won't touch existing data.

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
```

> **Note:** Chroma (the vector database) and Ollama are never touched by build commands.
> Your index is never deleted unless you explicitly run `docker-compose down -v`.

### Vault & Indexing

```bash
make index
# Re-indexes your entire vault from scratch (watcher handles day-to-day updates).

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

---

## WARNING: What deletes your index

```bash
docker-compose down -v   # ← THIS deletes all data (Chroma + Ollama volumes)
```

**Never run `down -v` unless you want to start from scratch.**
`make down` is safe — it does NOT pass `-v`.

If you accidentally delete the index, just run `make up` — it will rebuild automatically.

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
├── Makefile                ← all commands live here
├── docker-compose.yml
├── .env.example            ← only for port overrides
├── indexer/
│   ├── Dockerfile
│   └── indexer.py
├── watcher/
│   ├── Dockerfile
│   ├── watcher.py
│   └── indexer.py          ← shared lib
└── mcp-server/
    ├── Dockerfile
    └── mcp_server.py       ← MCP tools for Claude Desktop
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