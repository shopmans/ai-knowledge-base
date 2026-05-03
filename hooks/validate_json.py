#!/usr/bin/env python3
"""Validate knowledge base JSON entry files."""

import glob
import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}

ID_PATTERN = re.compile(r"^[a-z0-9]+-\d{8}-\d{3}$")
URL_PATTERN = re.compile(r"^https?://.+")


def validate_entry(data: dict, filepath: str) -> list[str]:
    """Validate a single parsed JSON dict and return a list of error messages."""
    errors: list[str] = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"missing required field: {field}")
        elif not isinstance(data[field], expected_type):
            errors.append(
                f"field '{field}' expected type {expected_type.__name__}, "
                f"got {type(data[field]).__name__}"
            )

    if errors:
        return errors

    entry_id = data["id"]
    if not ID_PATTERN.match(entry_id):
        errors.append(
            f"invalid id format: '{entry_id}' "
            f"(expected {{source}}-{{YYYYMMDD}}-{{NNN}})"
        )

    status = data["status"]
    if status not in VALID_STATUSES:
        errors.append(
            f"invalid status: '{status}' "
            f"(expected one of {sorted(VALID_STATUSES)})"
        )

    source_url = data["source_url"]
    if not URL_PATTERN.match(source_url):
        errors.append(f"invalid source_url format: '{source_url}'")

    summary = data["summary"]
    if len(summary) < 20:
        errors.append(
            f"summary too short: {len(summary)} chars (minimum 20)"
        )

    tags = data["tags"]
    if len(tags) < 1:
        errors.append("tags must contain at least 1 item")

    if "score" in data:
        score = data["score"]
        if not isinstance(score, (int, float)) or not (1 <= score <= 10):
            errors.append(f"score must be a number between 1 and 10, got: {score}")

    if "audience" in data:
        audience = data["audience"]
        if audience not in VALID_AUDIENCES:
            errors.append(
                f"invalid audience: '{audience}' "
                f"(expected one of {sorted(VALID_AUDIENCES)})"
            )

    return errors


def collect_paths(args: list[str]) -> list[Path]:
    """Expand glob patterns and collect concrete file paths."""
    paths: list[Path] = []
    for arg in args:
        if "*" in arg or "?" in arg:
            paths.extend(Path(p) for p in sorted(glob.glob(arg)))
        else:
            paths.append(Path(arg))
    return paths


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python hooks/validate_json.py <json_file> [json_file2 ...]",
              file=sys.stderr)
        sys.exit(1)

    paths = collect_paths(sys.argv[1:])

    if not paths:
        print("No files matched the given patterns.", file=sys.stderr)
        sys.exit(1)

    total = 0
    passed = 0
    failed = 0
    all_errors: list[str] = []

    for filepath in paths:
        total += 1
        label = str(filepath)

        if not filepath.exists():
            all_errors.append(f"[{label}] file not found")
            failed += 1
            continue

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            all_errors.append(f"[{label}] JSON parse error: {exc}")
            failed += 1
            continue

        if not isinstance(data, dict):
            all_errors.append(f"[{label}] top-level value must be a JSON object")
            failed += 1
            continue

        entry_errors = validate_entry(data, label)
        if entry_errors:
            for err in entry_errors:
                all_errors.append(f"[{label}] {err}")
            failed += 1
        else:
            passed += 1

    if all_errors:
        print("Validation errors:")
        for err in all_errors:
            print(f"  - {err}")
        print()

    print(f"Summary: {total} file(s), {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
