"""
Reviewer 审核节点

对 analyses 进行五维度加权评分，决定是否通过。
"""

import json

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

_REVIEW_SYSTEM = "你是严格的质量审核专家。请严格按 JSON 格式回复。"

_REVIEW_PROMPT = """\
请对以下知识条目进行五维度评分（每维 1-10 整数），输出 JSON:
{{
  "scores": {{
    "summary_quality": 1-10,
    "technical_depth": 1-10,
    "relevance": 1-10,
    "originality": 1-10,
    "formatting": 1-10
  }},
  "feedback": "具体改进建议；全部达标时填'质量合格'"
}}

待审核条目:
{item_json}"""

WEIGHTS = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}

PASS_THRESHOLD = 7.0
MAX_REVIEW_ITEMS = 5


def _weighted_score(scores: dict) -> float:
    total = 0.0
    for dim, weight in WEIGHTS.items():
        val = scores.get(dim, 5)
        val = max(1, min(10, int(val)))
        total += val * weight
    return round(total, 2)


def _ensure_dict(result: dict | list) -> dict:
    if isinstance(result, list):
        return result[0] if result else {}
    return result


def review_node(state: KBState) -> dict:
    print("[Reviewer] 开始审核 ...")

    analyses = state.get("analyses", [])
    cost_tracker = state.get("cost_tracker", {})
    iteration = state.get("iteration", 0)

    if iteration >= 2:
        print("[Reviewer] iteration >= 2，强制通过")
        return {
            "review_passed": True,
            "review_feedback": "达到最大迭代次数，强制通过",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    if not analyses:
        print("[Reviewer] analyses 为空，跳过审核")
        return {
            "review_passed": True,
            "review_feedback": "无待审核条目",
            "iteration": iteration + 1,
            "cost_tracker": cost_tracker,
        }

    batch = analyses[:MAX_REVIEW_ITEMS]
    total = len(batch)
    fail_feedbacks: list[str] = []
    scores_all: list[float] = []

    for i, item in enumerate(batch):
        item_json = json.dumps(item, ensure_ascii=False, indent=2)
        prompt = _REVIEW_PROMPT.format(item_json=item_json)
        try:
            result, usage = chat_json(prompt, system=_REVIEW_SYSTEM, temperature=0.1)
            cost_tracker = accumulate_usage(cost_tracker, usage)
            result = _ensure_dict(result)
            raw_scores = result.get("scores", {})
            feedback = result.get("feedback", "质量合格")
        except Exception as exc:
            print(f"[Reviewer] 条目 {i+1}/{total} LLM 调用失败: {exc}")
            scores_all.append(0)
            continue

        weighted = _weighted_score(raw_scores)
        scores_all.append(weighted)
        passed = weighted >= PASS_THRESHOLD
        title = item.get("title", f"#{i+1}")

        if passed:
            print(f"[Reviewer] 条目 {i+1}/{total} 通过 ({title}, score={weighted})", flush=True)
        else:
            print(f"[Reviewer] 条目 {i+1}/{total} 未通过 ({title}, score={weighted})", flush=True)
            fail_feedbacks.append(f"[{title}] {feedback}")

    if not scores_all:
        all_passed = True
        final_feedback = "全部审核异常，自动通过"
    else:
        all_passed = len(fail_feedbacks) == 0
        final_feedback = "质量合格" if all_passed else "; ".join(fail_feedbacks)

    avg = round(sum(scores_all) / len(scores_all), 2) if scores_all else 0
    status = "通过" if all_passed else "未通过"
    print(f"[Reviewer] 整体{status} (avg={avg}, iter={iteration})")

    return {
        "review_passed": all_passed,
        "review_feedback": final_feedback,
        "iteration": iteration + 1,
        "cost_tracker": cost_tracker,
    }
