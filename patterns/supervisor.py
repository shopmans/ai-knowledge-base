"""
Supervisor 监督模式 — Worker 产出 + Supervisor 审核 + 循环修正

Worker Agent: 接收任务，输出 JSON 格式的分析报告
Supervisor Agent: 对 Worker 输出进行质量审核（准确性/深度/格式）
审核循环: 不通过则带反馈重做，最多 3 轮
"""

import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflows.model_client import chat

WORKER_SYSTEM = """你是一个专业的 AI 技术分析师。
接收用户任务，输出 JSON 格式的分析报告，结构如下：
{
  "title": "报告标题",
  "summary": "简要概述（2-3句）",
  "key_points": ["要点1", "要点2", ...],
  "analysis": "深度分析（200字以上）",
  "conclusion": "结论与建议"
}
只输出 JSON，不要其他内容。"""

SUPERVISOR_SYSTEM = """你是一个严格的质量审核员。
审核下方的分析报告，从三个维度评分（1-10）：
- accuracy: 准确性（内容是否正确、有无事实错误）
- depth: 深度（分析是否深入、是否有洞见）
- format: 格式（JSON 结构是否完整、字段是否齐全）

输出 JSON:
{
  "accuracy": <1-10>,
  "depth": <1-10>,
  "format": <1-10>,
  "passed": <总分>=<21 时 true, 否则 false>,
  "score": <三个维度平均分, 四舍五入取整>,
  "feedback": "<不通过时给出具体改进意见，通过时写'质量合格'>"
}
只输出 JSON，不要其他内容。"""


def _parse_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        cleaned = "\n".join(lines[start:end])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue
    return None


def _run_worker(task: str, feedback: str | None = None) -> dict:
    prompt = task
    if feedback:
        prompt = (
            f"原始任务：{task}\n\n"
            f"上一次输出被审核退回，审核员反馈如下：\n{feedback}\n\n"
            f"请根据反馈改进，重新输出分析报告。"
        )
    text, _ = chat(prompt=prompt, system=WORKER_SYSTEM, temperature=0.5)
    result = _parse_json(text)
    if not isinstance(result, dict):
        return {"raw": text, "_parse_error": True}
    return result


def _run_supervisor(task: str, worker_output: dict) -> dict:
    prompt = (
        f"原始任务：{task}\n\n"
        f"待审核的分析报告：\n{json.dumps(worker_output, ensure_ascii=False, indent=2)}"
    )
    text, _ = chat(prompt=prompt, system=SUPERVISOR_SYSTEM, temperature=0.1)
    result = _parse_json(text)
    if result is None:
        return {"accuracy": 5, "depth": 5, "format": 5, "passed": False, "score": 5,
                "feedback": "审核输出解析失败，默认不通过"}
    score = result.get("score", 0)
    if not isinstance(score, int):
        dims = [result.get("accuracy", 5), result.get("depth", 5), result.get("format", 5)]
        score = round(sum(dims) / 3)
        result["score"] = score
    result["passed"] = score >= 7
    return result


def supervisor(task: str, max_retries: int = 3) -> dict:
    worker_output = None
    supervisor_result = None
    attempts = 0

    for attempt in range(1, max_retries + 1):
        attempts = attempt
        feedback = supervisor_result.get("feedback") if supervisor_result else None
        worker_output = _run_worker(task, feedback)

        if worker_output.get("_parse_error"):
            supervisor_result = {
                "passed": False, "score": 0,
                "feedback": "Worker 输出不是有效 JSON，请检查格式后重新输出。",
            }
            continue

        supervisor_result = _run_supervisor(task, worker_output)
        if supervisor_result.get("passed"):
            break

    result = {
        "output": worker_output,
        "attempts": attempts,
        "final_score": supervisor_result.get("score", 0) if supervisor_result else 0,
    }

    if attempts >= max_retries and not (supervisor_result or {}).get("passed"):
        result["warning"] = f"达到最大重试次数({max_retries})，强制返回最后结果。审核反馈：{supervisor_result.get('feedback', '无')}"

    return result


if __name__ == "__main__":
    test_tasks = [
        "分析 MCP (Model Context Protocol) 的技术架构和应用场景",
        "对比 LangChain 和 CrewAI 两个 AI Agent 框架的优缺点",
    ]
    for task in test_tasks:
        print(f"\n{'='*60}")
        print(f"Task: {task}")
        print("-" * 60)
        result = supervisor(task, max_retries=3)
        print(f"Attempts: {result['attempts']}")
        print(f"Score:    {result['final_score']}")
        if result.get("warning"):
            print(f"Warning:  {result['warning']}")
        output = result["output"]
        if isinstance(output, dict) and not output.get("_parse_error"):
            print(f"Title:    {output.get('title', 'N/A')}")
            print(f"Summary:  {output.get('summary', 'N/A')[:100]}...")
        else:
            print(f"Output:   {str(output)[:200]}...")
