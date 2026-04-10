#!/usr/bin/env python3
"""
mcp_server.py — MCP server that exposes your Obsidian vault as tools for Claude Code.

Tools exposed:
  - search_vault     → semantic search across all your notes
  - get_note         → read a specific note by path
  - list_tags        → list all tags used in your vault
  - list_notes       → list notes in a folder
  - save_note        → create a new note in your vault

Transport: HTTP/SSE.
Auth:      OAuth 2.0 (Authorization Code + PKCE) when OAUTH_PASSWORD + JWT_SECRET are set.
           Disabled automatically when running on localhost without those vars.
"""

import os
import re
import json
import time
import html as html_mod
import secrets
import hashlib
import base64
from pathlib import Path
from datetime import datetime

import chromadb
from chromadb.config import Settings
import ollama
import jwt as pyjwt
from mcp.server import Server
from indexer import index_file, get_collection as get_chroma
from mcp.server.sse import SseServerTransport
from mcp import types
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, HTMLResponse, RedirectResponse
import uvicorn

# ── Config ────────────────────────────────────────────────────
VAULT_PATH  = os.getenv("VAULT_PATH", "/vault")
CHROMA_HOST = os.getenv("CHROMA_HOST", "http://chroma:8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text-v2-moe")
TOP_K       = int(os.getenv("TOP_K_RESULTS", "3"))
PORT        = int(os.getenv("PORT", "3777"))

# ── OAuth 2.0 Config ──────────────────────────────────────────
# Set OAUTH_PASSWORD + JWT_SECRET to enable auth (required for remote/tunnel access).
# Leave empty for localhost-only usage (no auth).
OAUTH_PASSWORD  = os.getenv("OAUTH_PASSWORD", "").strip()
JWT_SECRET      = os.getenv("JWT_SECRET", "").strip()
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))
# Your public tunnel URL, e.g. https://xxx.trycloudflare.com — used in OAuth metadata.
PUBLIC_URL      = os.getenv("PUBLIC_URL", "").rstrip("/")
OAUTH_ENABLED   = bool(OAUTH_PASSWORD and JWT_SECRET)

# In-memory stores (single-process; cleared on container restart)
_pending_codes:     dict[str, dict] = {}  # code → {client_id, redirect_uri, challenge, …}
_registered_clients: dict[str, dict] = {} # client_id → {redirect_uris, client_name}


# ── OAuth Helpers ─────────────────────────────────────────────
def _base_url(request: Request) -> str:
    """Return the public-facing base URL (scheme + host, no trailing slash)."""
    if PUBLIC_URL:
        return PUBLIC_URL
    host = request.headers.get("host", f"localhost:{PORT}")
    # Cloudflare Tunnel sets CF-Visitor: {"scheme":"https"}
    cf = request.headers.get("cf-visitor", "")
    scheme = "https" if '"https"' in cf else request.url.scheme
    return f"{scheme}://{host}"


def _pkce_ok(verifier: str, challenge: str, method: str) -> bool:
    """Verify a PKCE code_verifier against a stored code_challenge."""
    if method == "S256":
        digest  = hashlib.sha256(verifier.encode()).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(encoded, challenge)
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    return False


def _issue_jwt(client_id: str) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {"sub": "owner", "client_id": client_id, "iat": now, "exp": now + JWT_EXPIRY_DAYS * 86400},
        JWT_SECRET,
        algorithm="HS256",
    )


def _valid_jwt(token: str) -> bool:
    try:
        pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except pyjwt.PyJWTError:
        return False


# ── OAuth Login Page ──────────────────────────────────────────
def _login_page(params: dict, error: str = "") -> str:
    """Render the OAuth login page with OAuth params preserved as hidden fields."""
    hidden = "\n".join(
        f'<input type="hidden" name="{html_mod.escape(k)}" value="{html_mod.escape(v)}">'
        for k, v in params.items()
    )
    err_html = f'<p class="err">{html_mod.escape(error)}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Second Brain — Authorize</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0d0d0d; color: #e5e5e5;
      display: flex; align-items: center; justify-content: center; min-height: 100vh;
    }}
    .card {{
      background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 14px;
      padding: 2.5rem 2rem; width: 340px; box-shadow: 0 8px 40px rgba(0,0,0,.5);
    }}
    h1 {{ font-size: 1.4rem; margin-bottom: .5rem; }}
    .sub {{ font-size: .85rem; color: #666; margin-bottom: 1.8rem; }}
    label {{ font-size: .8rem; color: #999; display: block; margin-bottom: .4rem; }}
    input[type=password] {{
      width: 100%; padding: .65rem .9rem; border: 1px solid #333;
      border-radius: 8px; background: #111; color: #e5e5e5;
      font-size: 1rem; outline: none; transition: border .2s;
    }}
    input[type=password]:focus {{ border-color: #6366f1; }}
    button {{
      width: 100%; margin-top: 1.2rem; padding: .75rem;
      background: #6366f1; border: none; border-radius: 8px;
      color: #fff; font-size: 1rem; font-weight: 600;
      cursor: pointer; transition: background .2s;
    }}
    button:hover {{ background: #4f46e5; }}
    .err {{ color: #f87171; margin-top: 1rem; font-size: .875rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🧠 Second Brain</h1>
    <p class="sub">Enter your password to authorize access.</p>
    <form method="POST">
      {hidden}
      <label for="pw">Password</label>
      <input id="pw" type="password" name="password" autocomplete="current-password" autofocus>
      <button type="submit">Authorize</button>
      {err_html}
    </form>
  </div>
</body>
</html>"""


# ── Clients ───────────────────────────────────────────────────
def get_chroma():
    host = CHROMA_HOST.replace("http://", "").replace("https://", "").split(":")[0]
    port = int(CHROMA_HOST.split(":")[-1])
    client = chromadb.HttpClient(
        host=host,
        port=port,
        settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name="second_brain",
        metadata={"hnsw:space": "cosine"}
    )


def embed(text: str) -> list[float]:
    client = ollama.Client(host=OLLAMA_HOST)
    return client.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


# ── MCP Server ────────────────────────────────────────────────
app = Server("second-brain")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_vault",
            description=(
                "Semantic search across all Obsidian vault notes. "
                "Returns the most relevant notes for a question or topic. "
                "Use this whenever the user asks about something they may have researched or noted before."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or topic to search for"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 3, max 10)",
                        "default": 3
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_note",
            description=(
                "Read the full content of a specific note from the vault. "
                "Use the relative path returned by search_vault."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the note (e.g. 'coding/docker-notes.md')"
                    }
                },
                "required": ["path"]
            }
        ),
        types.Tool(
            name="list_notes",
            description="List all notes in a vault folder.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Folder name to list (e.g. 'coding', 'research'). Leave empty for root.",
                        "default": ""
                    }
                }
            }
        ),
        types.Tool(
            name="list_tags",
            description="List all unique #tags used across the vault, with note counts.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="save_note",
            description=(
                "Save knowledge from the current conversation to the Obsidian vault. "
                "Before saving, analyze the conversation and decide:\n\n"
                "IF the conversation has BOTH conceptual discussion AND technical implementation:\n"
                "  → Save TWO notes without asking for confirmation:\n"
                "    Note 1 — Conceptual: the why, reasoning, decisions, insights, analogies\n"
                "    Note 2 — Technical: architecture, code, commands, configs, issues solved\n\n"
                "IF the conversation is ONLY conceptual (no code, no implementation):\n"
                "  → Save ONE note capturing: the problem, reasoning, insights, what was learned\n\n"
                "IF the conversation is ONLY technical (no deep reasoning, just implementation):\n"
                "  → Save ONE note capturing: code, commands, configs, file structure, issues solved\n\n"
                "Rules for every note:\n"
                "  - Never save raw conversation — always synthesize and structure\n"
                "  - Write as if the reader has zero context — useful months from now\n"
                "  - Extract relevant #tags based on the topic\n"
                "  - Choose the right folder: coding, research, astronomy, investments, business, conversations\n"
                "  - Include concrete examples, commands or code snippets if applicable\n"
                "  - Never ask for confirmation between saves — just execute"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the note"
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content of the note"
                    },
                    "folder": {
                        "type": "string",
                        "description": "Vault folder to save in (e.g. 'coding', 'research', 'astronomy')",
                        "default": "conversations"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tags for the note",
                        "default": []
                    }
                },
                "required": ["title", "content"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    # ── search_vault ────────────────────────────────────────────
    if name == "search_vault":
        query  = arguments["query"]
        top_k  = min(arguments.get("top_k", TOP_K), 10)

        try:
            collection = get_chroma()
            embedding  = embed(query)
            results    = collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"]
            )

            hits = []
            for i in range(len(results["ids"][0])):
                score = 1 - results["distances"][0][i]
                meta  = results["metadatas"][0][i]
                hits.append({
                    "title":      meta["title"],
                    "source":     meta["source"],
                    "tags":       meta.get("tags", ""),
                    "relevance":  f"{score:.0%}",
                    "folder":     meta.get("folder", ""),
                    "indexed_at": meta.get("indexed_at", ""),
                    "content":    results["documents"][0][i]
                })

            if not hits:
                return [types.TextContent(
                    type="text",
                    text="No relevant notes found for this query."
                )]

            output = f"Found {len(hits)} relevant note(s) for: '{query}'\n\n"
            for h in hits:
                output += f"{'─'*60}\n"
                output += f"📄 {h['title']}  ({h['source']})\n"
                output += f"   Relevance: {h['relevance']}"
                if h['tags']:
                    output += f"  |  Tags: {h['tags']}"
                output += f"\n\n{h['content']}\n\n"

            return [types.TextContent(type="text", text=output)]

        except Exception as e:
            return [types.TextContent(type="text", text=f"Search error: {e}")]

    # ── get_note ────────────────────────────────────────────────
    elif name == "get_note":
        rel_path = arguments["path"]
        filepath = Path(VAULT_PATH) / rel_path

        if not filepath.exists():
            return [types.TextContent(type="text", text=f"Note not found: {rel_path}")]

        content = filepath.read_text(encoding="utf-8", errors="ignore")
        return [types.TextContent(
            type="text",
            text=f"# {filepath.stem}\n**Path:** {rel_path}\n\n{content}"
        )]

    # ── list_notes ──────────────────────────────────────────────
    elif name == "list_notes":
        folder   = arguments.get("folder", "")
        base     = Path(VAULT_PATH) / folder if folder else Path(VAULT_PATH)

        if not base.exists():
            return [types.TextContent(type="text", text=f"Folder not found: {folder}")]

        files = sorted(base.rglob("*.md") if not folder else base.glob("*.md"))

        if not files:
            return [types.TextContent(type="text", text="No notes found.")]

        lines = [f"📁 {folder or 'vault root'} — {len(files)} note(s)\n"]
        for f in files:
            rel = f.relative_to(VAULT_PATH)
            lines.append(f"  • {rel}")

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── list_tags ───────────────────────────────────────────────
    elif name == "list_tags":
        vault  = Path(VAULT_PATH)
        counts: dict[str, int] = {}

        for md in vault.rglob("*.md"):
            content = md.read_text(encoding="utf-8", errors="ignore")
            for tag in re.findall(r'#([a-zA-Z][a-zA-Z0-9_/-]*)', content):
                counts[tag] = counts.get(tag, 0) + 1

        if not counts:
            return [types.TextContent(type="text", text="No tags found in vault.")]

        sorted_tags = sorted(counts.items(), key=lambda x: -x[1])
        lines = [f"🏷  {len(sorted_tags)} unique tags in vault\n"]
        for tag, count in sorted_tags:
            lines.append(f"  #{tag:<30} {count} note{'s' if count > 1 else ''}")

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── save_note ───────────────────────────────────────────────
    elif name == "save_note":
        title   = arguments["title"]
        content = arguments["content"]
        folder  = arguments.get("folder", "conversations")
        tags    = arguments.get("tags", [])

        # Build frontmatter
        now        = datetime.now()
        tags_yaml  = "\n".join([f"  - {t}" for t in tags])
        tags_inline = " ".join([f"#{t}" for t in tags])

        note = f"""---
title: "{title}"
date: {now.strftime('%Y-%m-%d')}
time: {now.strftime('%H:%M')}
tags:
{tags_yaml}
---

# {title}

{tags_inline}

{content}
"""
        # Slugify title for filename
        slug     = re.sub(r'[^\w\s-]', '', title.lower())
        slug     = re.sub(r'[\s_]+', '-', slug)[:60].strip('-')
        date_str = now.strftime("%Y-%m-%d")

        target_folder = Path(VAULT_PATH) / folder
        target_folder.mkdir(parents=True, exist_ok=True)

        filepath = target_folder / f"{date_str}-{slug}.md"
        counter  = 1
        while filepath.exists():
            filepath = target_folder / f"{date_str}-{slug}-{counter}.md"
            counter += 1

        filepath.write_text(note, encoding="utf-8")
        rel = filepath.relative_to(VAULT_PATH)

        index_file(filepath, get_chroma(), force=True)

        return [types.TextContent(
            type="text",
            text=f"✓ Note saved and indexed: {rel}"
        )]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ── MCP HTTP Routes ───────────────────────────────────────────
sse = SseServerTransport("/messages/")


async def handle_sse(request: Request) -> Response:
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())
    return Response()


async def handle_search(request: Request) -> JSONResponse:
    query = request.query_params.get("q", "").strip()
    top_k = min(int(request.query_params.get("top_k", TOP_K)), 10)

    if not query:
        return JSONResponse({"error": "Missing query param: q"}, status_code=400)

    try:
        collection = get_chroma()
        embedding  = embed(query)
        results    = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"]
        )

        hits = []
        for i in range(len(results["ids"][0])):
            score = 1 - results["distances"][0][i]
            meta  = results["metadatas"][0][i]
            hits.append({
                "title":    meta["title"],
                "source":   meta["source"],
                "tags":     meta.get("tags", ""),
                "relevance": f"{score:.0%}",
                "folder":   meta.get("folder", ""),
                "content":  results["documents"][0][i]
            })

        return JSONResponse({"query": query, "results": hits})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── OAuth 2.0 Routes ──────────────────────────────────────────
async def oauth_metadata(request: Request) -> JSONResponse:
    """RFC 8414 — OAuth Authorization Server Metadata."""
    base = _base_url(request)
    return JSONResponse({
        "issuer":                                base,
        "authorization_endpoint":               f"{base}/oauth/authorize",
        "token_endpoint":                        f"{base}/oauth/token",
        "registration_endpoint":                 f"{base}/oauth/register",
        "response_types_supported":              ["code"],
        "grant_types_supported":                 ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported":      ["S256"],
    })


async def oauth_register(request: Request) -> JSONResponse:
    """RFC 7591 — Dynamic Client Registration. Accept any client (PKCE handles security)."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    client_id = secrets.token_urlsafe(16)
    _registered_clients[client_id] = {
        "redirect_uris": body.get("redirect_uris", []),
        "client_name":   body.get("client_name", "unknown"),
    }
    return JSONResponse({
        "client_id":                  client_id,
        "client_id_issued_at":        int(time.time()),
        "redirect_uris":              body.get("redirect_uris", []),
        "grant_types":                ["authorization_code"],
        "response_types":             ["code"],
        "token_endpoint_auth_method": "none",
    }, status_code=201)


async def oauth_authorize(request: Request) -> Response:
    """GET → show login form. POST → validate password, issue auth code, redirect."""
    if request.method == "GET":
        params = dict(request.query_params)
        return HTMLResponse(_login_page(params))

    # POST — process login
    form   = await request.form()
    params = {k: v for k, v in form.items() if k != "password"}
    password = form.get("password", "")

    if not OAUTH_PASSWORD or not secrets.compare_digest(password.encode(), OAUTH_PASSWORD.encode()):
        return HTMLResponse(_login_page(params, "Wrong password — try again."), status_code=401)

    redirect_uri          = params.get("redirect_uri", "")
    state                 = params.get("state", "")
    code_challenge        = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    client_id             = params.get("client_id", "")

    if not redirect_uri:
        return JSONResponse({"error": "invalid_request", "error_description": "Missing redirect_uri"}, status_code=400)

    # Purge stale codes while we're here
    now = int(time.time())
    for k in [k for k, v in _pending_codes.items() if v["expires_at"] < now]:
        del _pending_codes[k]

    code = secrets.token_urlsafe(32)
    _pending_codes[code] = {
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "code_challenge":        code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires_at":            now + 600,  # 10-minute window
    }

    sep      = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"
    return RedirectResponse(location, status_code=302)


async def oauth_token(request: Request) -> JSONResponse:
    """Exchange authorization code for a JWT access token."""
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    if body.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    code          = body.get("code", "")
    redirect_uri  = body.get("redirect_uri", "")
    client_id     = body.get("client_id", "")
    code_verifier = body.get("code_verifier", "")

    entry = _pending_codes.pop(code, None)
    if not entry:
        return JSONResponse({"error": "invalid_grant", "error_description": "Unknown or expired code"}, status_code=400)

    if int(time.time()) > entry["expires_at"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "Code expired"}, status_code=400)

    if redirect_uri and not secrets.compare_digest(
        redirect_uri.ljust(max(len(redirect_uri), len(entry["redirect_uri"]))),
        entry["redirect_uri"].ljust(max(len(redirect_uri), len(entry["redirect_uri"]))),
    ):
        return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)

    if entry["code_challenge"] and not _pkce_ok(code_verifier, entry["code_challenge"], entry["code_challenge_method"]):
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

    token = _issue_jwt(client_id or entry["client_id"])
    return JSONResponse({
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   JWT_EXPIRY_DAYS * 86400,
    })


# ── Starlette App ─────────────────────────────────────────────
_OAUTH_PATHS = frozenset({
    "/.well-known/oauth-authorization-server",
    "/oauth/authorize",
    "/oauth/token",
    "/oauth/register",
})

starlette_app = Starlette(
    routes=[
        # OAuth discovery + flow (always public)
        Route("/.well-known/oauth-authorization-server", endpoint=oauth_metadata),
        Route("/oauth/authorize", endpoint=oauth_authorize, methods=["GET", "POST"]),
        Route("/oauth/token",     endpoint=oauth_token,     methods=["POST"]),
        Route("/oauth/register",  endpoint=oauth_register,  methods=["POST"]),
        # MCP endpoints (protected when OAUTH_ENABLED)
        Route("/sse",    endpoint=handle_sse),
        Route("/search", endpoint=handle_search),
        Mount("/messages/", app=sse.handle_post_message),
    ]
)


# ── OAuth ASGI Middleware ─────────────────────────────────────
def _bearer_token(scope: dict) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


async def oauth_protected_app(scope, receive, send):
    """ASGI wrapper that validates JWT on MCP routes. Skips auth for OAuth endpoints."""
    if scope["type"] != "http":
        await starlette_app(scope, receive, send)
        return

    path   = scope.get("path", "")
    method = scope.get("method", "")

    # OAuth / discovery endpoints are always open
    if path in _OAUTH_PATHS or method == "OPTIONS":
        await starlette_app(scope, receive, send)
        return

    # Validate JWT on MCP routes
    token = _bearer_token(scope)
    if not _valid_jwt(token):
        resource_root = PUBLIC_URL or f"http://localhost:{PORT}"
        www_auth = (
            f'Bearer realm="second-brain",'
            f' resource_metadata_uri="{resource_root}/.well-known/oauth-authorization-server"'
        )
        response = Response(
            content=json.dumps({"error": "Unauthorized"}),
            status_code=401,
            media_type="application/json",
            headers={"WWW-Authenticate": www_auth},
        )
        await response(scope, receive, send)
        return

    await starlette_app(scope, receive, send)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    if OAUTH_ENABLED:
        print(f"[auth] OAuth 2.0 enabled — public URL: {PUBLIC_URL or '(auto-detect from request)'}")
    else:
        print("[auth] OAuth disabled — running without authentication (localhost mode)")

    app_to_run = oauth_protected_app if OAUTH_ENABLED else starlette_app
    uvicorn.run(app_to_run, host="0.0.0.0", port=PORT)
