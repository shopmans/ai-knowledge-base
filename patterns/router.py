"""
Router 路由模式 — 两层意图分类 + 分发处理

第一层：关键词快速匹配（零成本）
第二层：LLM 分类兜底（处理模糊意图）
"""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflows.model_client import chat, chat_json

ARTICLES_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "articles"
INDEX_PATH = ARTICLES_DIR / "index.json"

GITHUB_SIGNALS = [
    "github", "trending", "热门仓库", "热门项目", "开源项目",
    "repo", "star", "stars", "fork", "仓库", "趋势",
    "开源", "github trending",
]

KNOWLEDGE_SIGNALS = [
    "知识库", "文章", "采集", "摘要", "评级", "评分", "标签",
    "之前", "历史", "已有", "采集过", "收藏",
    "agent", "mcp", "pipeline", "llm", "ai", "模型",
    "框架", "教程", "工具", "部署", "微调", "训练",
    "搜索", "查找", "最近", "最新", "推荐",
]

INTENT_SYSTEM_PROMPT = """你是一个意图分类器。根据用户输入，判断意图属于以下三类之一：

- github_search: 用户想搜索 GitHub 开源项目/仓库
- knowledge_query: 用户想查询本地知识库中已采集的内容
- general_chat: 普通闲聊或与上述无关的问题

只返回 JSON: {"intent": "github_search" | "knowledge_query" | "general_chat"}"""


def _keyword_match(query: str) -> str | None:
    q = query.lower()
    hit_github = any(kw in q for kw in GITHUB_SIGNALS)
    hit_knowledge = any(kw in q for kw in KNOWLEDGE_SIGNALS)
    if hit_github:
        return "github_search"
    if hit_knowledge:
        return "knowledge_query"
    return None


def _llm_classify(query: str) -> str:
    result, _ = chat_json(
        prompt=query,
        system=INTENT_SYSTEM_PROMPT,
        temperature=0.0,
        max_tokens=100,
    )
    intent = result.get("intent", "general_chat") if isinstance(result, dict) else "general_chat"
    if intent not in ("github_search", "knowledge_query", "general_chat"):
        intent = "general_chat"
    return intent


def classify(query: str) -> str:
    intent = _keyword_match(query)
    if intent:
        return intent
    return _llm_classify(query)


def _handle_github_search(query: str) -> str:
    api_url = "https://api.github.com/search/repositories?q={}&sort=stars&per_page=5"
    encoded_q = urllib.parse.quote(query)
    url = api_url.format(encoded_q)
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Knowledge-Base/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"GitHub 搜索失败: {e}"

    items = data.get("items", [])
    if not items:
        return f"未找到与「{query}」相关的 GitHub 仓库。"

    lines = [f"GitHub 搜索结果（{query}）：\n"]
    for i, repo in enumerate(items, 1):
        lines.append(
            f"{i}. {repo['full_name']}  ⭐{repo.get('stargazers_count', 0)}\n"
            f"   {repo.get('description', '无描述')}\n"
            f"   {repo.get('html_url', '')}"
        )
    return "\n".join(lines)


def _handle_knowledge_query(query: str) -> str:
    if not INDEX_PATH.exists():
        articles = []
        for f in ARTICLES_DIR.glob("*.json"):
            try:
                articles.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    else:
        try:
            articles = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            articles = []

    if not articles:
        return "知识库暂无文章。"

    q_lower = query.lower()
    scored: list[tuple[int, dict]] = []
    for art in articles:
        score = 0
        title = (art.get("title") or "").lower()
        summary = (art.get("summary_zh") or art.get("summary") or "").lower()
        tags = " ".join(art.get("tags") or []).lower()
        for word in q_lower.split():
            if word in title:
                score += 3
            if word in tags:
                score += 2
            if word in summary:
                score += 1
        if score > 0:
            scored.append((score, art))

    if not scored:
        return f"未在知识库中找到与「{query}」相关的内容。"

    scored.sort(key=lambda x: x[0], reverse=True)
    lines = [f"知识库检索结果（{query}）：\n"]
    for i, (sc, art) in enumerate(scored[:5], 1):
        lines.append(
            f"{i}. [{art.get('source', '')}] {art.get('title', '无标题')} (相关度:{sc})\n"
            f"   {art.get('summary_zh') or art.get('summary', '无摘要')}\n"
            f"   {art.get('url', '')}"
        )
    return "\n".join(lines)


def _handle_general_chat(query: str) -> str:
    text, _ = chat(
        prompt=query,
        system="你是一个友善且专业的 AI 助手，擅长回答各类问题。",
    )
    return text


HANDLERS = {
    "github_search": _handle_github_search,
    "knowledge_query": _handle_knowledge_query,
    "general_chat": _handle_general_chat,
}


def route(query: str) -> str:
    intent = classify(query)
    handler = HANDLERS[intent]
    return handler(query)


if __name__ == "__main__":
    test_queries = [
        "帮我搜索 GitHub 上的 MCP 相关项目",
        "知识库里有哪些 agent 相关的文章？",
        "今天天气怎么样？",
        "github trending 有什么热门仓库？",
        "介绍一下 Transformer 架构",
    ]
    for q in test_queries:
        intent = classify(q)
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        print(f"Intent: {intent}")
        try:
            result = route(q)
            print(f"A: {result[:300]}{'...' if len(result) > 300 else ''}")
        except Exception as e:
            print(f"Error: {e}")
