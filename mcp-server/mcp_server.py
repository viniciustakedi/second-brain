#!/usr/bin/env python3
"""
mcp_server.py — MCP server that exposes your Obsidian vault as tools for Claude Code.

Tools exposed:
  - search_vault     → semantic search across all your notes
  - get_note         → read a specific note by path
  - list_tags        → list all tags used in your vault
  - list_notes       → list notes in a folder
  - save_note        → create a new note in your vault

Claude Code connects to this via stdio (local) or HTTP (remote).
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

import chromadb
from chromadb.config import Settings
import ollama
from mcp.server import Server
from indexer import index_file, get_collection as get_chroma
from mcp.server.sse import SseServerTransport
from mcp import types
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
import uvicorn

# ── Config ────────────────────────────────────────────────────
VAULT_PATH  = os.getenv("VAULT_PATH", "/vault")
CHROMA_HOST = os.getenv("CHROMA_HOST", "http://chroma:8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
TOP_K       = int(os.getenv("TOP_K_RESULTS", "3"))

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


# ── Entry point (HTTP/SSE) ────────────────────────────────────
PORT = int(os.getenv("PORT", "3777"))

sse = SseServerTransport("/messages/")


async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())
    return Response()


async def handle_search(request: Request):
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
                "title":      meta["title"],
                "source":     meta["source"],
                "tags":       meta.get("tags", ""),
                "relevance":  f"{score:.0%}",
                "folder":     meta.get("folder", ""),
                "content":    results["documents"][0][i]
            })

        return JSONResponse({"query": query, "results": hits})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


starlette_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/search", endpoint=handle_search),
        Mount("/messages/", app=sse.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(starlette_app, host="0.0.0.0", port=PORT)
