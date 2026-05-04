"""Four-step knowledge base automation pipeline.

Steps:
    1. **Collect** — Fetch AI-related content from GitHub Search API and RSS feeds.
    2. **Analyze** — Call an LLM to summarise, score, and tag each item.
    3. **Organize** — Deduplicate, normalise, and validate the results.
    4. **Save** — Write each article as an individual JSON file under ``knowledge/articles/``.

Usage::

    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from model_client import (
    LLMResponse,
    Usage,
    calculate_cost,
    chat_with_retry,
    create_provider,
    estimate_tokens,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT_DIR / "knowledge" / "raw"
ARTICLES_DIR = ROOT_DIR / "knowledge" / "articles"

# ---------------------------------------------------------------------------
# GitHub search queries
# ---------------------------------------------------------------------------

_GITHUB_QUERIES = [
    "AI agent framework",
    "LLM fine-tuning",
    "RAG retrieval augmented generation",
    "MCP model context protocol",
    "multimodal model",
]

# ---------------------------------------------------------------------------
# RSS feed sources (loaded from rss_sources.yaml)
# ---------------------------------------------------------------------------

def _load_rss_sources(yaml_path: Path | None = None) -> list[dict[str, Any]]:
    """Load enabled RSS sources from the YAML config.

    Args:
        yaml_path: Path to ``rss_sources.yaml``. Defaults to
            ``pipeline/rss_sources.yaml`` next to this file.

    Returns:
        List of feed dicts with ``name``, ``url``, and ``category`` keys.
    """
    path = yaml_path or Path(__file__).resolve().parent / "rss_sources.yaml"
    if not path.is_file():
        logger.warning("RSS config not found: %s", path)
        return []
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return [
        {"name": s["name"], "url": s["url"], "category": s.get("category", "")}
        for s in sources
        if s.get("enabled", False)
    ]


_RSS_FEEDS: list[dict[str, Any]] | None = None


def _get_rss_feeds() -> list[dict[str, Any]]:
    """Return the RSS feed list, lazy-loaded on first access."""
    global _RSS_FEEDS
    if _RSS_FEEDS is None:
        _RSS_FEEDS = _load_rss_sources()
        logger.info("Loaded %d enabled RSS feeds from config", len(_RSS_FEEDS))
    return _RSS_FEEDS

# ---------------------------------------------------------------------------
# LLM analysis prompt template
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """\
You are an AI technology analyst. Analyse the following article/project and return ONLY valid JSON (no markdown fences) with these fields:

{
  "summary_zh": "Chinese summary in 1-2 sentences",
  "highlights": ["highlight 1", "highlight 2"],
  "score": 85,
  "tags": ["tag1", "tag2"],
  "category": "framework|tool|research|tutorial|news",
  "recommendation": "must-read|recommended|optional"
}

Scoring: technical novelty (30%), practical value (30%), community impact (20%), content quality (20%).
Category must be one of: framework, tool, research, tutorial, news.
Recommendation must be one of: must-read, recommended, optional.
Return ONLY the JSON object, no other text."""

_ANALYSIS_USER = """\
Title: {title}
Source: {source}
Description: {description}
URL: {url}

Analyse this item and return the JSON."""

# ---------------------------------------------------------------------------
# Step 1: Collect
# ---------------------------------------------------------------------------


def _collect_github(client: httpx.Client, limit: int) -> list[dict[str, Any]]:
    """Fetch trending AI repos from GitHub Search API.

    Args:
        client: Shared httpx client.
        limit: Maximum items to return.

    Returns:
        List of raw item dicts.
    """
    items: list[dict[str, Any]] = []
    per_query = max(limit // len(_GITHUB_QUERIES), 1)

    for query in _GITHUB_QUERIES:
        if len(items) >= limit:
            break
        try:
            resp = client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_query,
                },
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("GitHub search failed for %r: %s", query, exc)
            continue

        for repo in body.get("items", []):
            items.append({
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "description": repo.get("description") or "",
                "source": "github",
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language") or "",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })

        time.sleep(1)

    logger.info("Collected %d items from GitHub", len(items))
    return items[:limit]


def _parse_rss_xml(text: str, feed_name: str) -> list[dict[str, Any]]:
    """Extract items from an RSS XML feed using simple regex parsing.

    Args:
        text: Raw RSS XML content.
        feed_name: Name of the feed for provenance.

    Returns:
        List of raw item dicts.
    """
    items: list[dict[str, Any]] = []

    for match in re.finditer(r"<item>(.*?)</item>", text, re.DOTALL):
        block = match.group(1)

        def _extract(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
            return m.group(1).strip() if m else ""

        title = _extract("title")
        link = _extract("link")
        description = re.sub(r"<[^>]+>", "", _extract("description"))

        title = re.sub(r"^<!\[CDATA\[\s*(.*?)\s*\]\]>$", r"\1", title)
        description = re.sub(r"^<!\[CDATA\[\s*(.*?)\s*\]\]>$", r"\1", description)

        if title and link:
            items.append({
                "title": title,
                "url": link,
                "description": description,
                "source": feed_name,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })

    return items


def _collect_rss(client: httpx.Client, limit: int) -> list[dict[str, Any]]:
    """Fetch AI articles from configured RSS feeds.

    Args:
        client: Shared httpx client.
        limit: Maximum items to return.

    Returns:
        List of raw item dicts.
    """
    items: list[dict[str, Any]] = []

    for feed in _get_rss_feeds():
        try:
            resp = client.get(feed["url"], timeout=30.0)
            resp.raise_for_status()
            feed_items = _parse_rss_xml(resp.text, feed["name"])
            items.extend(feed_items)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("RSS fetch failed for %s: %s", feed["name"], exc)

    logger.info("Collected %d items from RSS", len(items))
    return items[:limit]


def step_collect(sources: list[str], limit: int) -> list[dict[str, Any]]:
    """Step 1: Collect raw items from all specified sources.

    Args:
        sources: List of source names (``"github"``, ``"rss"``).
        limit: Per-source item cap.

    Returns:
        Combined list of raw item dicts.
    """
    logger.info("Step 1 — Collect (sources=%s, limit=%d)", sources, limit)
    all_items: list[dict[str, Any]] = []

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        if "github" in sources:
            all_items.extend(_collect_github(client, limit))
        if "rss" in sources:
            all_items.extend(_collect_rss(client, limit))

    logger.info("Collected %d items total", len(all_items))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = RAW_DIR / f"collect_{ts}.json"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Raw data saved to %s", raw_path)

    return all_items


# ---------------------------------------------------------------------------
# Step 2: Analyze
# ---------------------------------------------------------------------------


def _parse_analysis_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from the LLM response text.

    Tries direct ``json.loads`` first, then strips markdown code fences,
    then searches for the first ``{`` … ``}`` brace-delimited block.

    Args:
        text: Raw LLM response.

    Returns:
        Parsed dict, or ``None`` on failure.
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM analysis JSON: %s", text[:120])
    return None


def step_analyze(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 2: Analyse each item with the LLM.

    Args:
        items: Raw items from :func:`step_collect`.

    Returns:
        Items augmented with ``analysis`` and ``usage`` fields.
    """
    logger.info("Step 2 — Analyze (%d items)", len(items))
    provider = create_provider()
    total_usage = Usage()
    analysed: list[dict[str, Any]] = []

    for idx, item in enumerate(items, 1):
        logger.info("  [%d/%d] %s", idx, len(items), item.get("title", "?")[:60])

        prompt = _ANALYSIS_USER.format(
            title=item.get("title", ""),
            source=item.get("source", ""),
            description=item.get("description", ""),
            url=item.get("url", ""),
        )

        try:
            resp: LLMResponse = chat_with_retry(
                [{"role": "system", "content": _ANALYSIS_SYSTEM},
                 {"role": "user", "content": prompt}],
                provider=provider,
                temperature=0.3,
                max_tokens=2048,
            )

            content = resp.content
            if not content:
                reasoning = (
                    resp.raw.get("choices", [{}])[0]
                    .get("message", {})
                    .get("reasoning_content", "")
                )
                content = reasoning

            total_usage.prompt_tokens += resp.usage.prompt_tokens
            total_usage.completion_tokens += resp.usage.completion_tokens
            total_usage.total_tokens += resp.usage.total_tokens
        except Exception as exc:
            logger.error("  Analysis failed for %s: %s", item.get("title", "?")[:40], exc)
            content = ""

        analysis = _parse_analysis_json(content) or {}
        item["analysis"] = analysis
        item["_llm_raw"] = content
        analysed.append(item)

    cost = calculate_cost(total_usage, provider.provider_name)
    logger.info(
        "Analysis complete: %d tokens, cost $%.6f",
        total_usage.total_tokens,
        cost,
    )
    return analysed


# ---------------------------------------------------------------------------
# Step 3: Organize
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {"framework", "tool", "research", "tutorial", "news"}
_VALID_RECOMMENDATIONS = {"must-read", "recommended", "optional"}

_STANDARD_TAGS: set[str] = {
    "backend", "frontend", "devops", "database", "security",
    "architecture", "algorithm", "testing", "performance",
    "ai", "ml", "networking", "cloud", "mobile", "linux",
    "python", "java", "go", "rust", "javascript", "typescript",
    "kubernetes", "docker", "ci-cd", "monitoring", "design-pattern",
    "distributed-systems", "microservices", "api", "storage",
    "concurrency", "caching", "messaging", "web", "data-structure",
    "compiler", "os", "blockchain", "iot", "embedded",
}

_TAG_ALIASES: dict[str, str] = {
    "deep-learning": "ml",
    "machine-learning": "ml",
    "machine learning": "ml",
    "机器学习": "ml",
    "深度学习": "ml",
    "neural-network": "ml",
    "神经网络": "ml",
    "nlp": "ai",
    "natural language processing": "ai",
    "llm": "ai",
    "large language model": "ai",
    "大语言模型": "ai",
    "computer-vision": "ai",
    "computer vision": "ai",
    "计算机视觉": "ai",
    "rag": "ai",
    "retrieval augmented generation": "ai",
    "agent": "ai",
    "ai agent": "ai",
    "agents": "ai",
    "mcp": "ai",
    "model context protocol": "ai",
    "fine-tuning": "ml",
    "fine tuning": "ml",
    "微调": "ml",
    "rlhf": "ml",
    "transformer": "ml",
    "diffusion": "ai",
    "generative-ai": "ai",
    "生成式ai": "ai",
    "prompt-engineering": "ai",
    "提示工程": "ai",
    "prompt": "ai",
    "gpt": "ai",
    "openai": "ai",
    "claude": "ai",
    "huggingface": "ai",
    "langchain": "ai",
    "embedding": "ai",
    "向量数据库": "database",
    "vector database": "database",
    "pytorch": "python",
    "tensorflow": "python",
    "jax": "python",
    "react": "frontend",
    "vue": "frontend",
    "node": "frontend",
    "nodejs": "frontend",
    "kubernetes": "devops",
    "docker": "devops",
    "container": "devops",
    "容器": "devops",
    "ci/cd": "ci-cd",
    "devops": "devops",
    "serverless": "cloud",
    "cloud-native": "cloud",
    "云原生": "cloud",
    "微服务": "microservices",
    "microservice": "microservices",
    "rest": "api",
    "graphql": "api",
    "grpc": "api",
    "http": "networking",
    "tcp": "networking",
    "kafka": "messaging",
    "消息队列": "messaging",
    "redis": "caching",
    "缓存": "caching",
    "负载均衡": "networking",
    "监控": "monitoring",
    "日志": "monitoring",
    "链路追踪": "monitoring",
    "security": "security",
    "安全": "security",
    "authentication": "security",
    "encryption": "security",
    "sandboxing": "security",
    "testing": "testing",
    "测试": "testing",
    "版本控制": "devops",
    "git": "devops",
    "emacs": "linux",
    "editor": "linux",
    "cli": "linux",
    "终端工具": "linux",
    "开发者工具": "devops",
    "ai编码助手": "ai",
    "编码代理": "ai",
    "framework": "architecture",
    "frameworks": "architecture",
    "开源框架": "architecture",
    "ai工程": "ai",
    "ai integration": "ai",
    "ai-integration": "ai",
    "desktop app": "frontend",
    "automation": "devops",
    "automation": "devops",
    "discussion": "architecture",
    "multimodal": "ai",
    "多模态": "ai",
    "知识库": "database",
    "ai工作流": "ai",
    "多语言教程": "ai",
    "微软开源": "ai",
}


def _normalise_tags(raw_tags: list[str], max_tags: int = 5) -> list[str]:
    """Map raw LLM-generated tags to the standard tag set.

    Args:
        raw_tags: Tags produced by the LLM analysis step.
        max_tags: Maximum number of tags to keep.

    Returns:
        Deduplicated list of standard tags.
    """
    mapped: list[str] = []
    seen: set[str] = set()

    for tag in raw_tags:
        key = tag.lower().strip()
        std = _TAG_ALIASES.get(key)
        if std is None:
            if key in _STANDARD_TAGS:
                std = key
            else:
                continue
        if std not in seen:
            seen.add(std)
            mapped.append(std)

    return mapped[:max_tags]

_SOURCE_ALIASES: dict[str, str] = {
    "github": "github",
    "Hacker News — AI": "hacker",
    "Hacker News — Best (AI/ML)": "hacker",
    "Hacker News — Machine Learning": "hacker",
    "Lobsters — AI/ML": "lobsters",
    "OpenAI Blog": "openai",
    "Anthropic": "anthropic",
    "Hugging Face Blog": "huggingface",
    "arXiv — cs.AI (Artificial Intelligence)": "arxiv-ai",
    "arXiv — cs.CL (Computation and Language)": "arxiv-cl",
    "arXiv — cs.LG (Machine Learning)": "arxiv-lg",
    "机器之心": "jiqizhixin",
    "量子位": "qbitai",
}


def _generate_id(item: dict[str, Any], seq: int = 1) -> str:
    """Generate a deterministic ID from source, date, and sequence number.

    Args:
        item: An analysed item dict.
        seq: Sequence number within the collection run.

    Returns:
        ID in format ``{source}-{YYYYMMDD}-{NNN}``.
    """
    source = _SOURCE_ALIASES.get(item.get("source", ""), item.get("source", "unknown"))
    source = re.sub(r"[^a-z0-9]", "", source.lower()) or "unknown"
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{source}-{date_str}-{seq:03d}"


def step_organize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 3: Deduplicate and normalise items into standard article format.

    Args:
        items: Analysed items from :func:`step_analyze`.

    Returns:
        Deduplicated list of standard article dicts.
    """
    logger.info("Step 3 — Organize (%d items)", len(items))

    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for item in items:
        url = item.get("url", "")
        if url in seen_urls:
            logger.debug("Skipping duplicate: %s", item.get("title", ""))
            continue
        seen_urls.add(url)
        deduped.append(item)

    articles: list[dict[str, Any]] = []

    for seq, item in enumerate(deduped, start=1):
        aid = _generate_id(item, seq=seq)

        analysis = item.get("analysis", {})
        raw_score = analysis.get("score", 0)
        if not isinstance(raw_score, (int, float)):
            raw_score = 0
        raw_score = max(0, min(100, int(raw_score)))
        score = max(1, min(10, round(raw_score / 10)))

        category = analysis.get("category", "news")
        if category not in _VALID_CATEGORIES:
            category = "news"

        recommendation = analysis.get("recommendation", "optional")
        if recommendation not in _VALID_RECOMMENDATIONS:
            recommendation = "optional"

        highlights = analysis.get("highlights", [])
        if not isinstance(highlights, list):
            highlights = [str(highlights)]

        raw_tags = analysis.get("tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = [str(raw_tags)]
        tags = _normalise_tags(raw_tags)
        if not tags:
            tags = ["ai"]

        summary_zh = analysis.get("summary_zh", "")

        now_iso = datetime.now(timezone.utc).isoformat()

        article = {
            "id": aid,
            "title": item.get("title", ""),
            "source_url": item.get("url", ""),
            "source": item.get("source", ""),
            "summary": summary_zh,
            "summary_zh": summary_zh,
            "highlights": highlights,
            "score": score,
            "tags": tags,
            "category": category,
            "recommendation": recommendation,
            "status": "draft",
            "timestamp": now_iso,
            "language": item.get("language", ""),
            "stars": item.get("stars", 0),
            "collected_at": item.get("collected_at", ""),
            "updated_at": now_iso,
        }

        articles.append(article)

    articles.sort(key=lambda a: a["score"], reverse=True)

    logger.info(
        "Organized: %d unique articles (avg score %.1f)",
        len(articles),
        sum(a["score"] for a in articles) / max(len(articles), 1),
    )
    return articles


# ---------------------------------------------------------------------------
# Step 4: Save
# ---------------------------------------------------------------------------


def step_save(articles: list[dict[str, Any]]) -> list[Path]:
    """Step 4: Write each article as a separate JSON file.

    Files are saved under ``knowledge/articles/{YYYY-MM-DD}/`` with the pattern
    ``{source}_{score}_{YYYYMMDD}_{seq}.json`` where *seq* starts at 1 per run.

    Args:
        articles: Standardised articles from :func:`step_organize`.

    Returns:
        List of written file paths.
    """
    logger.info("Step 4 — Save (%d articles)", len(articles))
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    written: list[Path] = []
    for seq, article in enumerate(articles, start=1):
        source = _SOURCE_ALIASES.get(article.get("source", ""), article.get("source", "unknown"))
        filename = f"{source}_{article['score']}_{date_str}_{seq}.json"
        path = ARTICLES_DIR / filename
        path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
        logger.debug("  Saved %s", filename)

    logger.info("Saved %d articles to %s", len(written), ARTICLES_DIR)
    return written


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def run_pipeline(
    sources: list[str],
    limit: int = 20,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Execute the full four-step pipeline.

    Args:
        sources: Data sources to collect from.
        limit: Maximum items per source.
        dry_run: If ``True``, skip LLM analysis and saving.

    Returns:
        The final list of organised article dicts.
    """
    logger.info("=" * 60)
    logger.info("Pipeline started  sources=%s  limit=%d  dry_run=%s", sources, limit, dry_run)
    logger.info("=" * 60)

    items = step_collect(sources, limit)
    if not items:
        logger.warning("No items collected. Pipeline aborted.")
        return []

    if dry_run:
        logger.info("Dry-run mode — skipping analyze / organize / save")
        for item in items:
            print(f"  [{item.get('source')}] {item.get('title', '')[:80]}")
            print(f"    {item.get('url', '')}")
        return []

    analysed = step_analyze(items)
    organised = step_organize(analysed)
    step_save(organised)

    logger.info("=" * 60)
    logger.info("Pipeline complete — %d articles saved", len(organised))
    logger.info("=" * 60)
    return organised


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="AI knowledge base automation pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python pipeline/pipeline.py --sources github,rss --limit 20
  python pipeline/pipeline.py --sources github --limit 5
  python pipeline/pipeline.py --sources rss --limit 10 --dry-run
  python pipeline/pipeline.py --verbose
""",
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="Comma-separated sources: github, rss (default: github,rss)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max items per source (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect only, skip LLM analysis and saving",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main() -> None:
    """Entry point for the CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    valid = {"github", "rss"}
    invalid = set(sources) - valid
    if invalid:
        parser.error(f"Unknown sources: {invalid}. Choose from {valid}")

    run_pipeline(sources=sources, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
