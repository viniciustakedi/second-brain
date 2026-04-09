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
│  │ (manual) │   │ (always) │            │           │
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
After that, `make up` starts the stack without a full re-index (Chroma keeps your vectors across restarts).

**First time** (empty Chroma) or after `docker-compose down -v`, build the index once:

```bash
make index
```

Day-to-day changes are picked up by the **watcher** while the stack is running.

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

### 5. Optional: reach your local MCP from the cloud (tunnel)

Claude Desktop on the same Mac can use `http://localhost:3777/sse` directly. If you want **Cursor**, **Anthropic**, or another product to attach to **this same MCP** without deploying your own VPS, run a **secure tunnel** from your machine while the stack is up (`make up`). That forwards a public HTTPS URL to port **3777** on localhost.

**Typical options:** **[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)** (`cloudflared`, free, stable URL on your own subdomain) or **[ngrok](https://ngrok.com/)**. Both forward HTTPS on the internet to port **3777** on your Mac while `make up` is running.

#### Ngrok (quick test)

1. Install and authenticate ngrok per their docs.
2. `ngrok http 3777`
3. Use `https://<ngrok-host>/sse` in the remote MCP config.

#### Cloudflare Tunnel (subdomain on your zone)

Good fit when DNS already lives on Cloudflare: no open inbound ports on your router, TLS at the edge, fixed hostname.

1. Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and run `cloudflared tunnel login`.
2. In [Zero Trust](https://one.dash.cloudflare.com/) → **Networks** → **Tunnels**, create a tunnel and add a **public hostname**: e.g. `mcp` → `http://127.0.0.1:3777` (same port as `MCP_PORT` / default **3777**). Publish the route so Cloudflare creates the **CNAME** on your zone.
3. Run the tunnel on the machine where Docker is listening (often `cloudflared tunnel run <name>`).
4. Point OpenAI / Cursor / etc. at:

   ```text
   https://mcp.yourdomain.com/sse
   ```

   Example remote MCP config (shape varies by product):

   ```json
   {
     "mcpServers": {
       "second-brain": {
         "type": "sse",
         "url": "https://mcp.yourdomain.com/sse"
       }
     }
   }
   ```

**Security (read this):** the tunnel URL is on the public internet. Without extra checks, **anyone who obtains the URL** could use your MCP (search/read/write notes). Stack defenses in order:

1. **Prefer no public URL when you can** — `localhost` on the same machine, or **Tailscale** / VPN so only your devices reach the Mac.

2. **Edge / Zero Trust (before traffic hits Docker)**  
   - **Cloudflare:** [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) on that hostname — e.g. allow only your email, or a **[service token](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/)** for machine-to-machine calls. Interactive login helps browsers; **cloud MCP connectors** must send whatever Access expects (often **service token** headers) or they will get **403**. If the vendor cannot send Cloudflare headers, protect the app with **`MCP_SECRET`** (below) instead of relying on Access for that hostname.  
   - **Ngrok:** [traffic policies](https://ngrok.com/docs/traffic-policy/) (OAuth, IP allowlists, JWT, etc.) where your plan allows.

3. **App secret (`MCP_SECRET`)** — Set in `.env` (see `.env.example`). This server then requires `Authorization: Bearer <same secret>` or `X-MCP-Secret: <same secret>` on `/sse`, `/messages/…`, and `/search`. **Use this for OpenAI/cloud MCP if the product lets you attach headers** — it does not depend on Cloudflare’s UI. If the client supports **no** custom headers, you are limited to edge policies that still allow that client (hard) or accepting more risk; check the vendor’s MCP docs.

   ```bash
   # .env — generate once: openssl rand -hex 32
   MCP_SECRET=your-long-random-string
   ```

   Example config when headers are supported:

   ```json
   {
     "mcpServers": {
       "second-brain": {
         "type": "sse",
         "url": "https://mcp.yourdomain.com/sse",
         "headers": {
           "Authorization": "Bearer your-long-random-string"
         }
       }
     }
   }
   ```

**Never commit** tunnel URLs, tokens, or `.env`. For day-to-day private use, **localhost** (or Tailscale) is simpler and safer than a public HTTPS endpoint.

---

## Moving to another machine

Your vault (notes) lives in iCloud — already synced.
The Docker project folder can also be in iCloud — also synced.

On the new machine:

1. Make sure Docker Desktop is installed
2. Add `VAULT_PATH` to `~/.zshrc` and run `source ~/.zshrc`
3. Run `make up` from inside the `second-brain/` folder
4. Register the MCP server in Claude Desktop (step 4 above)

Docker volumes (Chroma, Ollama) are **local** to that machine unless you copy them yourself. After `make up`, run `make index` once on the new machine so search has data (your `.md` files already sync via iCloud).

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
```

> **Note:** Chroma (the vector database) and Ollama are never touched by build commands.
> Your index is never deleted unless you explicitly run `docker-compose down -v`.

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

---

## WARNING: What deletes your index

```bash
docker-compose down -v   # ← THIS deletes all data (Chroma + Ollama volumes)
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