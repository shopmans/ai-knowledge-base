"""MCP Server for the local AI knowledge base.

Exposes three tools over JSON-RPC 2.0 via stdio:

    search_articles(keyword, limit=5) — full-text search across titles and summaries.
    get_article(article_id)           — retrieve a single article by its ID.
    knowledge_stats()                 — aggregated statistics about the knowledge base.

No third-party dependencies — uses only the Python standard library.

Usage::

    python pipeline/mcp_knowledge_server.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"

_articles_cache: list[dict[str, Any]] | None = None


def _load_articles() -> list[dict[str, Any]]:
    """Load all article JSON files from the knowledge base directory."""
    global _articles_cache
    if _articles_cache is not None:
        return _articles_cache

    articles: list[dict[str, Any]] = []
    if not ARTICLES_DIR.is_dir():
        _articles_cache = articles
        return articles

    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            articles.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[warn] skip {path.name}: {exc}", file=sys.stderr)

    _articles_cache = articles
    return articles


def _invalidate_cache() -> None:
    """Force a reload on next access."""
    global _articles_cache
    _articles_cache = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _search_articles(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Search articles by keyword in title, summary, tags, and highlights.

    Args:
        arguments: Dict with optional ``keyword`` (str) and ``limit`` (int).

    Returns:
        Matching articles, newest first, capped at *limit*.
    """
    keyword = (arguments.get("keyword") or "").strip().lower()
    limit = min(int(arguments.get("limit", 5)), 50)

    articles = _load_articles()

    if not keyword:
        articles_sorted = sorted(
            articles, key=lambda a: a.get("score", 0), reverse=True
        )
        return articles_sorted[:limit]

    matches: list[tuple[int, dict[str, Any]]] = []
    for article in articles:
        score = 0
        title = (article.get("title") or "").lower()
        summary = (article.get("summary_zh") or "").lower()
        description = (article.get("description") or "").lower()
        tags_text = " ".join(article.get("tags", [])).lower()
        highlights_text = " ".join(article.get("highlights", [])).lower()

        if keyword in title:
            score += 10
        if keyword in tags_text:
            score += 8
        if keyword in summary:
            score += 5
        if keyword in highlights_text:
            score += 3
        if keyword in description:
            score += 2

        if score > 0:
            matches.append((score, article))

    matches.sort(key=lambda pair: (pair[0], pair[1].get("score", 0)), reverse=True)
    return [article for _, article in matches[:limit]]


def _get_article(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Retrieve a single article by its ``id`` field.

    Args:
        arguments: Dict with ``article_id`` key.

    Returns:
        The full article dict, or ``None`` if not found.
    """
    target_id = arguments.get("article_id", "")
    for article in _load_articles():
        if article.get("id") == target_id:
            return article
    return None


def _knowledge_stats(_arguments: dict[str, Any]) -> dict[str, Any]:
    """Compute aggregated statistics over the knowledge base.

    Returns:
        Dict with totals, source/category distributions, and top tags.
    """
    articles = _load_articles()

    sources = dict(Counter(a.get("source", "unknown") for a in articles))
    categories = dict(Counter(a.get("category", "unknown") for a in articles))
    recommendations = dict(
        Counter(a.get("recommendation", "unknown") for a in articles)
    )

    tag_counter: Counter[str] = Counter()
    total_score = 0
    for article in articles:
        for tag in article.get("tags", []):
            tag_counter[tag] += 1
        total_score += article.get("score", 0)

    count = len(articles)
    return {
        "total_articles": count,
        "avg_score": round(total_score / count, 1) if count else 0,
        "sources": dict(
            sorted(sources.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "categories": dict(
            sorted(categories.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "recommendations": recommendations,
        "top_tags": [tag for tag, _ in tag_counter.most_common(20)],
        "tag_counts": dict(tag_counter.most_common(20)),
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_articles",
        "description": (
            "Search the AI knowledge base by keyword. "
            "Searches across article titles, summaries, tags, and highlights. "
            "Returns a ranked list of matching articles."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (matched against title, summary, tags, highlights).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5, max: 50).",
                    "default": 5,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": (
            "Retrieve the full content of a specific article by its ID. "
            "Use this after search_articles to get complete details."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "The unique article ID (UUID string).",
                },
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": (
            "Get aggregated statistics about the knowledge base: "
            "total articles, source distribution, category breakdown, and popular tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

_TOOL_DISPATCH: dict[str, Any] = {
    "search_articles": _search_articles,
    "get_article": _get_article,
    "knowledge_stats": _knowledge_stats,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------


def _make_response(request_id: Any, result: Any) -> str:
    """Build a JSON-RPC 2.0 success response."""
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        ensure_ascii=False,
    )


def _make_error(
    request_id: Any, code: int, message: str, data: Any = None
) -> str:
    """Build a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": error},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# MCP protocol handlers
# ---------------------------------------------------------------------------


_SERVER_INFO = {
    "name": "ai-knowledge-base",
    "version": "1.0.0",
}

_CAPABILITIES = {
    "tools": {
        "listChanged": False,
    },
}


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """Handle the MCP ``initialize`` request."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": _CAPABILITIES,
        "serverInfo": _SERVER_INFO,
    }


def _handle_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    """Handle the MCP ``tools/list`` request."""
    return {"tools": _TOOL_DEFINITIONS}


def _handle_tools_call(params: dict[str, Any], request_id: Any) -> str:
    """Handle the MCP ``tools/call`` request."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    handler = _TOOL_DISPATCH.get(tool_name)
    if handler is None:
        return _make_error(
            request_id,
            -32601,
            f"Unknown tool: {tool_name!r}",
            data={"available": list(_TOOL_DISPATCH)},
        )

    try:
        result = handler(arguments)
        if result is None:
            return _make_error(
                request_id, -32001, "Article not found", data={"arguments": arguments}
            )
        return _make_response(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]})
    except Exception as exc:
        return _make_error(
            request_id, -32603, f"Tool execution error: {exc}", data={"tool": tool_name}
        )


# ---------------------------------------------------------------------------
# Main stdio loop
# ---------------------------------------------------------------------------


def _process_message(msg: dict[str, Any]) -> str | None:
    """Route a single JSON-RPC message and return the response string."""
    method = msg.get("method", "")
    request_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _make_response(request_id, _handle_initialize(params))

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _make_response(request_id, _handle_tools_list(params))

    if method == "tools/call":
        return _handle_tools_call(params, request_id)

    return _make_error(request_id, -32601, f"Method not found: {method!r}")


def main() -> None:
    """Read JSON-RPC messages from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(
                _make_error(None, -32700, "Parse error: invalid JSON") + "\n"
            )
            sys.stdout.flush()
            continue

        response = _process_message(msg)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
