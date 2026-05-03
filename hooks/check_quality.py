#!/usr/bin/env python3
"""Quality scoring for knowledge base entries across five dimensions."""

import glob
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHINESE_BUZZWORDS: list[str] = [
    "赋能", "抓手", "闭环", "打通", "全链路",
    "底层逻辑", "颗粒度", "对齐", "拉通", "沉淀",
    "强大的", "革命性的",
]

ENGLISH_BUZZWORDS: list[str] = [
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "disruptive", "industry-leading",
    "next-generation", "world-class", "best-in-class",
    "state-of-the-art",
]

TECH_KEYWORDS: list[str] = [
    "API", "SDK", "微服务", "容器", "Kubernetes", "Docker",
    "CI/CD", "DevOps", "机器学习", "深度学习", "神经网络",
    "分布式", "缓存", "消息队列", "负载均衡", "中间件",
    "数据库", "索引", "事务", "并发", "序列化", "反序列化",
    "算法", "复杂度", "架构", "设计模式", "重构", "测试",
    "监控", "日志", "链路追踪", "服务网格", "网关",
    "HTTP", "TCP", "gRPC", "REST", "GraphQL",
    "Python", "Java", "Go", "Rust", "TypeScript",
    "React", "Vue", "Node", "Linux", "Git",
    "serverless", "cloud-native", "microservice",
    "authentication", "authorization", "encryption",
    "scalability", "latency", "throughput",
]

STANDARD_TAGS: set[str] = {
    "backend", "frontend", "devops", "database", "security",
    "architecture", "algorithm", "testing", "performance",
    "ai", "ml", "networking", "cloud", "mobile", "linux",
    "python", "java", "go", "rust", "javascript", "typescript",
    "kubernetes", "docker", "ci-cd", "monitoring", "design-pattern",
    "distributed-systems", "microservices", "api", "storage",
    "concurrency", "caching", "messaging", "web", "data-structure",
    "compiler", "os", "blockchain", "iot", "embedded",
}

FORMAT_FIELDS: list[str] = ["id", "title", "source_url", "status", "timestamp"]

FIELD_POINTS: int = 4


@dataclass
class DimensionScore:
    name: str
    score: float
    max_score: float
    details: str


@dataclass
class QualityReport:
    filepath: str
    title: str
    dimensions: list[DimensionScore] = field(default_factory=list)
    total_score: float = 0.0
    grade: str = "C"

    @property
    def max_total(self) -> float:
        return sum(d.max_score for d in self.dimensions)


def _score_summary(data: dict) -> DimensionScore:
    summary: str = data.get("summary", "")
    length = len(summary)
    score = 0.0
    details_parts: list[str] = []

    if length >= 50:
        score += 20
        details_parts.append(f"长度 {length} 字(满分)")
    elif length >= 20:
        ratio = (length - 20) / 30
        score += 10 + 10 * ratio
        details_parts.append(f"长度 {length} 字(基本分)")
    else:
        ratio = max(length, 0) / 20
        score += 10 * ratio
        details_parts.append(f"长度仅 {length} 字(不足)")

    keyword_hits = [kw for kw in TECH_KEYWORDS if kw.lower() in summary.lower()]
    bonus = min(len(keyword_hits) * 1.5, 5)
    score += bonus
    if keyword_hits:
        details_parts.append(f"技术关键词 +{bonus:.1f}: {', '.join(keyword_hits[:5])}")

    score = min(score, 25)
    return DimensionScore("摘要质量", score, 25, "; ".join(details_parts))


def _score_depth(data: dict) -> DimensionScore:
    raw = data.get("score")
    if raw is None:
        return DimensionScore("技术深度", 0, 25, "缺少 score 字段")
    if not isinstance(raw, (int, float)):
        return DimensionScore("技术深度", 0, 25, f"score 类型错误: {type(raw).__name__}")
    raw = max(1, min(10, raw))
    mapped = (raw - 1) / 9 * 25
    return DimensionScore("技术深度", mapped, 25, f"原始 score={raw} → {mapped:.1f}/25")


def _score_format(data: dict) -> DimensionScore:
    score = 0.0
    missing: list[str] = []
    empty: list[str] = []

    for f in FORMAT_FIELDS:
        if f not in data:
            missing.append(f)
        elif not data[f]:
            empty.append(f)
        else:
            score += FIELD_POINTS

    parts: list[str] = []
    if missing:
        parts.append(f"缺失字段: {', '.join(missing)}")
    if empty:
        parts.append(f"空值字段: {', '.join(empty)}")
    if not parts:
        parts.append("全部格式字段完整")

    return DimensionScore("格式规范", score, 20, "; ".join(parts))


def _score_tags(data: dict) -> DimensionScore:
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 0, 15, "tags 不是列表")

    count = len(tags)
    invalid = [t for t in tags if t not in STANDARD_TAGS]
    score = 0.0
    parts: list[str] = []

    if count == 0:
        parts.append("无标签")
    elif count > 5:
        score += 5
        parts.append(f"标签过多({count} 个)")
    else:
        count_score = min(count, 3) / 3 * 10
        score += count_score
        parts.append(f"{count} 个标签")

    if invalid:
        penalty = len(invalid) * 2
        score = max(0, score - penalty)
        parts.append(f"非标准标签 -{penalty}: {', '.join(invalid[:5])}")
    elif tags:
        parts.append("标签均在标准列表中")

    score = min(score, 15)
    return DimensionScore("标签精度", score, 15, "; ".join(parts))


def _score_buzzwords(data: dict) -> DimensionScore:
    text_fields = [
        data.get("title", ""),
        data.get("summary", ""),
    ]
    full_text = " ".join(text_fields).lower()
    found: list[str] = []

    for word in CHINESE_BUZZWORDS:
        if word in full_text:
            found.append(word)

    lower_text = full_text
    for word in ENGLISH_BUZZWORDS:
        if word.lower() in lower_text:
            found.append(word)

    penalty = len(found) * 3
    score = max(0, 15 - penalty)

    if found:
        detail = f"发现 {len(found)} 个空洞词 -{penalty}: {', '.join(found)}"
    else:
        detail = "未检测到空洞词"

    return DimensionScore("空洞词检测", score, 15, detail)


def _compute_grade(total: float) -> str:
    if total >= 80:
        return "A"
    if total >= 60:
        return "B"
    return "C"


def evaluate(data: dict, filepath: str) -> QualityReport:
    report = QualityReport(filepath=filepath, title=data.get("title", "<无标题>"))
    report.dimensions = [
        _score_summary(data),
        _score_depth(data),
        _score_format(data),
        _score_tags(data),
        _score_buzzwords(data),
    ]
    report.total_score = sum(d.score for d in report.dimensions)
    report.grade = _compute_grade(report.total_score)
    return report


def _progress_bar(score: float, max_score: float, width: int = 30) -> str:
    ratio = score / max_score if max_score > 0 else 0
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {score:5.1f}/{max_score:.0f}"


def _grade_color(grade: str) -> str:
    return {"A": "\033[32m", "B": "\033[33m", "C": "\033[31m"}.get(grade, "")


RESET = "\033[0m"
BOLD = "\033[1m"


def print_report(report: QualityReport) -> None:
    color = _grade_color(report.grade)
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {report.title}{RESET}")
    print(f"  文件: {report.filepath}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    for dim in report.dimensions:
        bar = _progress_bar(dim.score, dim.max_score)
        print(f"  {dim.name:<8s} {bar}")
        print(f"           {dim.details}")

    total_bar = _progress_bar(report.total_score, report.max_total)
    print(f"  {'─' * 60}")
    print(f"  {'总  分':<8s} {total_bar}")
    print(f"  {'等  级':<8s} {color}{BOLD}{report.grade}{RESET}")
    print()


def collect_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        if "*" in arg or "?" in arg:
            paths.extend(Path(p) for p in sorted(glob.glob(arg)))
        else:
            paths.append(Path(arg))
    return paths


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python hooks/check_quality.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    paths = collect_paths(sys.argv[1:])

    if not paths:
        print("No files matched the given patterns.", file=sys.stderr)
        sys.exit(1)

    reports: list[QualityReport] = []
    has_c_grade = False

    for filepath in paths:
        label = str(filepath)
        if not filepath.exists():
            print(f"[ERROR] file not found: {label}", file=sys.stderr)
            has_c_grade = True
            continue

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[ERROR] JSON parse error in {label}: {exc}", file=sys.stderr)
            has_c_grade = True
            continue

        if not isinstance(data, dict):
            print(f"[ERROR] {label}: top-level value must be a JSON object",
                  file=sys.stderr)
            has_c_grade = True
            continue

        report = evaluate(data, label)
        reports.append(report)
        print_report(report)

        if report.grade == "C":
            has_c_grade = True

    if reports:
        print(f"{BOLD}{'=' * 60}{RESET}")
        print(f"{BOLD}  汇总: {len(reports)} 篇文章{RESET}")
        avg = sum(r.total_score for r in reports) / len(reports)
        print(f"  平均分: {avg:.1f}/100")

        grade_counts = {"A": 0, "B": 0, "C": 0}
        for r in reports:
            grade_counts[r.grade] += 1
        print(f"  等级分布: A={grade_counts['A']}  B={grade_counts['B']}  C={grade_counts['C']}")
        print(f"{BOLD}{'=' * 60}{RESET}\n")

    sys.exit(1 if has_c_grade else 0)


if __name__ == "__main__":
    main()
