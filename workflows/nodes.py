"""
LangGraph 工作流节点定义

5 个纯函数节点：collect → analyze → organize → review → save
每个节点接收 KBState，返回 dict（部分状态更新）。
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from workflows.model_client import accumulate_usage, chat, chat_json
from workflows.state import KBState

GITHUB_API = "https://api.github.com"
ARTICLES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge", "articles")
)
AI_SEARCH_QUERY = (
    "artificial intelligence OR machine learning OR LLM OR agent OR RAG OR MCP"
)


def _ensure_dict(result: dict | list) -> dict:
    if isinstance(result, list):
        return result[0] if result else {}
    return result


# ---------------------------------------------------------------------------
# collect_node
# ---------------------------------------------------------------------------

def collect_node(state: KBState) -> dict:
    print("[CollectNode] 开始采集 GitHub AI 相关仓库 ...")

    sources: list[dict] = []
    seen_urls: set[str] = set()

    for sort_field in ("stars", "updated"):
        params = urllib.parse.urlencode(
            {
                "q": AI_SEARCH_QUERY,
                "sort": sort_field,
                "order": "desc",
                "per_page": 15,
            }
        )
        url = f"{GITHUB_API}/search/repositories?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "ai-knowledge-base",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"[CollectNode] GitHub API 请求失败 (sort={sort_field}): {exc}")
            continue

        for item in data.get("items", []):
            html_url = item.get("html_url", "")
            if html_url in seen_urls:
                continue
            seen_urls.add(html_url)
            sources.append(
                {
                    "title": item.get("full_name", ""),
                    "url": html_url,
                    "source": "github",
                    "description": item.get("description") or "",
                    "stars": item.get("stargazers_count", 0),
                    "language": item.get("language") or "",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    print(f"[CollectNode] 采集完成，共 {len(sources)} 条")
    cost_tracker = state.get("cost_tracker", {})
    return {"sources": sources, "cost_tracker": cost_tracker}


# ---------------------------------------------------------------------------
# analyze_node
# ---------------------------------------------------------------------------

_ANALYZE_SYSTEM = "你是专业的 AI 技术分析师。请严格按 JSON 格式回复。"

_ANALYZE_PROMPT = """\
请对以下 AI 技术项目进行深度分析，输出 JSON（不要包含任何多余文本）:
{{
  "summary_zh": "200 字以内的中文技术摘要",
  "highlights": ["亮点1", "亮点2", "亮点3"],
  "score": 0.0 到 1.0 之间的浮点数评分,
  "tags": ["标签1", "标签2", "标签3"],
  "category": "framework | tool | tutorial | research | application",
  "recommendation": "recommended | optional | skip"
}}

项目信息：
- 名称: {title}
- 描述: {desc}
- 语言: {lang}
- Stars: {stars}
- URL: {url}"""


def _analyze_one(src: dict, cost_tracker: dict) -> tuple[dict, dict]:
    prompt = _ANALYZE_PROMPT.format(
        title=src.get("title", ""),
        desc=src.get("description", ""),
        lang=src.get("language", ""),
        stars=src.get("stars", 0),
        url=src.get("url", ""),
    )
    result, usage = chat_json(prompt, system=_ANALYZE_SYSTEM)
    result = _ensure_dict(result)
    cost_tracker = accumulate_usage(cost_tracker, usage)
    result.update(
        {
            "title": src.get("title", ""),
            "url": src.get("url", ""),
            "source": src.get("source", "github"),
            "collected_at": src.get("collected_at", ""),
        }
    )
    return result, cost_tracker


def analyze_node(state: KBState) -> dict:
    print("[AnalyzeNode] 开始 LLM 分析 ...")

    sources = state.get("sources", [])
    cost_tracker = state.get("cost_tracker", {})
    analyses: list[dict] = []

    for src in sources:
        try:
            result, cost_tracker = _analyze_one(src, cost_tracker)
            analyses.append(result)
        except Exception as exc:
            print(f"[AnalyzeNode] 分析失败 ({src.get('title', '')}): {exc}")
            analyses.append(
                {
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "source": src.get("source", "github"),
                    "summary_zh": src.get("description", ""),
                    "highlights": [],
                    "score": 0.3,
                    "tags": [],
                    "category": "uncategorized",
                    "recommendation": "skip",
                    "collected_at": src.get("collected_at", ""),
                }
            )

    print(f"[AnalyzeNode] 分析完成，共 {len(analyses)} 条")
    return {"analyses": analyses, "cost_tracker": cost_tracker}


# ---------------------------------------------------------------------------
# organize_node
# ---------------------------------------------------------------------------

_FIX_SYSTEM = "你是专业的 AI 技术分析师。根据反馈修正条目，严格按 JSON 格式回复。"

_FIX_PROMPT = """\
根据审核反馈修正以下知识条目，输出修正后的完整 JSON:
{{
  "summary_zh": "修正后的中文摘要",
  "highlights": ["亮点1", "亮点2", "亮点3"],
  "score": 0.0 到 1.0 之间的浮点数评分,
  "tags": ["标签1", "标签2", "标签3"],
  "category": "framework | tool | tutorial | research | application",
  "recommendation": "recommended | optional | skip"
}}

审核反馈: {feedback}

原始条目:
{item_json}"""


def _build_entry(item: dict, seq: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    float_score = float(item.get("score", 0))
    return {
        "id": f"github-{date_str}-{seq:03d}",
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "source_url": item.get("url", ""),
        "source": item.get("source", "github"),
        "summary_zh": item.get("summary_zh", ""),
        "summary": item.get("summary_zh", ""),
        "highlights": item.get("highlights", []),
        "score": max(1, min(10, round(float_score * 10))),
        "tags": item.get("tags", []),
        "category": item.get("category", "uncategorized"),
        "recommendation": item.get("recommendation", "optional"),
        "status": "draft",
        "timestamp": now,
        "collected_at": item.get("collected_at", ""),
        "updated_at": now,
    }


def organize_node(state: KBState) -> dict:
    print("[OrganizeNode] 开始整理 ...")

    analyses = state.get("analyses", [])
    cost_tracker = state.get("cost_tracker", {})
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")

    # 1) 过滤低分
    filtered = [a for a in analyses if float(a.get("score", 0)) >= 0.6]

    # 2) 按 URL 去重
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in filtered:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            deduped.append(item)

    # 3) 如果 iteration > 0 且有 feedback，用 LLM 定向修正
    if iteration > 0 and feedback:
        for i, item in enumerate(deduped):
            prompt = _FIX_PROMPT.format(
                feedback=feedback,
                item_json=json.dumps(item, ensure_ascii=False, indent=2),
            )
            try:
                fixed, usage = chat_json(prompt, system=_FIX_SYSTEM)
                fixed = _ensure_dict(fixed)
                cost_tracker = accumulate_usage(cost_tracker, usage)
                item.update(fixed)
            except Exception as exc:
                print(f"[OrganizeNode] LLM 修正失败 ({item.get('title', '')}): {exc}")

    # 4) 格式化为标准条目
    articles = [_build_entry(item, idx + 1) for idx, item in enumerate(deduped)]

    print(f"[OrganizeNode] 整理完成，{len(analyses)} → {len(articles)} 条")
    return {"articles": articles, "cost_tracker": cost_tracker}


# ---------------------------------------------------------------------------
# review_node
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = "你是严格的质量审核专家。请严格按 JSON 格式回复。"

_REVIEW_PROMPT = """\
请对以下知识条目进行四维度审核评分，输出 JSON:
{{
  "passed": true 或 false（overall_score >= 0.7 为 true），
  "overall_score": 0.0 到 1.0 之间的综合评分,
  "feedback": "具体改进建议；通过时填'质量合格'",
  "scores": {{
    "summary_quality": 0.0 到 1.0,
    "tag_accuracy": 0.0 到 1.0,
    "category_fit": 0.0 到 1.0,
    "consistency": 0.0 到 1.0
  }}
}}

待审核条目:
{article_json}"""


def _review_one(article: dict, cost_tracker: dict) -> tuple[dict, dict]:
    prompt = _REVIEW_PROMPT.format(
        article_json=json.dumps(article, ensure_ascii=False, indent=2),
    )
    result, usage = chat_json(prompt, system=_REVIEW_SYSTEM)
    result = _ensure_dict(result)
    cost_tracker = accumulate_usage(cost_tracker, usage)
    return result, cost_tracker


def review_node(state: KBState) -> dict:
    print("[ReviewNode] 开始审核 ...")

    articles = state.get("articles", [])
    cost_tracker = state.get("cost_tracker", {})
    iteration = state.get("iteration", 0)

    if iteration >= 2:
        print("[ReviewNode] iteration >= 2，强制通过")
        return {
            "review_passed": True,
            "review_feedback": "达到最大迭代次数，强制通过",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    total = len(articles)
    passed_count = 0
    fail_feedbacks: list[str] = []
    scores: list[float] = []

    for i, article in enumerate(articles):
        try:
            result, cost_tracker = _review_one(article, cost_tracker)
            item_passed = bool(result.get("passed", False))
            overall = float(result.get("overall_score", 0))
            feedback = result.get("feedback", "质量合格")
        except Exception as exc:
            print(f"[ReviewNode] 条目 {i+1}/{total} 审核失败: {exc}")
            item_passed = True
            overall = 0
            feedback = f"审核异常自动通过: {exc}"

        if item_passed:
            passed_count += 1
        else:
            fail_feedbacks.append(f"[{article.get('id', i+1)}] {feedback}")
        scores.append(overall)
        print(f"[ReviewNode] 条目 {i+1}/{total} {'通过' if item_passed else '未通过'} (score={overall:.2f})", flush=True)

    avg_score = sum(scores) / len(scores) if scores else 0
    passed = passed_count == total
    feedback = "质量合格" if passed else "; ".join(fail_feedbacks)

    status = "通过" if passed else "未通过"
    print(f"[ReviewNode] 整体{status} ({passed_count}/{total} passed, avg={avg_score:.2f}, iter={iteration})")
    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": cost_tracker,
    }


# ---------------------------------------------------------------------------
# save_node
# ---------------------------------------------------------------------------

def save_node(state: KBState) -> dict:
    print("[SaveNode] 开始保存 ...")

    articles = state.get("articles", [])
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    saved_files: list[str] = []
    for article in articles:
        article_id = article.get("id", f"unknown_{int(time.time())}")
        safe_name = article_id.replace("/", "_")
        filepath = os.path.join(ARTICLES_DIR, f"{safe_name}.json")
        with open(filepath, "w", encoding="utf-8") as fp:
            json.dump(article, fp, ensure_ascii=False, indent=2)
        saved_files.append(filepath)

    # 更新 index.json
    index_path = os.path.join(ARTICLES_DIR, "index.json")
    existing_index: list[dict] = []
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as fp:
                existing_index = json.load(fp)
        except (json.JSONDecodeError, IOError):
            existing_index = []

    existing_urls = {entry.get("url") for entry in existing_index}
    for article in articles:
        if article.get("url") not in existing_urls:
            existing_index.append(
                {
                    "id": article.get("id", ""),
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "score": article.get("score", 0),
                    "category": article.get("category", ""),
                    "tags": article.get("tags", []),
                    "timestamp": article.get("timestamp", ""),
                }
            )

    with open(index_path, "w", encoding="utf-8") as fp:
        json.dump(existing_index, fp, ensure_ascii=False, indent=2)

    print(f"[SaveNode] 保存完成，{len(saved_files)} 个文件，索引 {len(existing_index)} 条")
    return {}
