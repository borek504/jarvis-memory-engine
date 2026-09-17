from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from .canonical import digest_json
from .model import freshness_state


TYPE_PRIORITY = {
    "RULE": 0,
    "DECISION": 1,
    "GOAL": 2,
    "PREFERENCE": 3,
    "PROJECT_STATE": 4,
    "FACT": 5,
    "REFERENCE": 6,
}


def _text(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "memory_id": record["memory_id"],
            "memory_type": record["memory_type"],
            "domain": record["domain"],
            "project_scope": record["project_scope"],
            "subject_type": record["subject_type"],
            "subject_id": record["subject_id"],
            "slot_key": record["slot_key"],
            "content": record["content"],
        },
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()


def search(
    records: Iterable[dict[str, Any]],
    *,
    query: str | None = None,
    domain: str | None = None,
    project_scope: str | None = None,
    subject_id: str | None = None,
    current_only: bool = True,
    usable_only: bool = True,
    allow_sensitive: bool = False,
    sensitive_purpose: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if query is not None and not isinstance(query, str):
        raise TypeError("query must be a string or None")
    if allow_sensitive and (
        not isinstance(sensitive_purpose, str) or not sensitive_purpose.strip()
    ):
        raise ValueError("allow_sensitive requires a non-empty sensitive_purpose")

    normalized = (query or "").casefold().strip()
    tokens = [token for token in normalized.split() if token]
    result: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    for record in records:
        if current_only and record["lifecycle"] != "CURRENT":
            continue
        if domain is not None and record["domain"] != domain:
            continue
        if project_scope is not None and record["project_scope"] != project_scope:
            continue
        if subject_id is not None and record["subject_id"] != subject_id:
            continue
        if record["sensitivity"] == "RESTRICTED_LOCAL":
            continue
        if record["sensitivity"] == "SENSITIVE" and not allow_sensitive:
            continue

        state = freshness_state(
            freshness_class=record["freshness_class"],
            lifecycle=record["lifecycle"],
            aged_after_utc=record["aged_after_utc"],
            stale_after_utc=record["stale_after_utc"],
            validated_at_utc=record["validated_at_utc"],
            now=now,
        )
        if usable_only and state in {"STALE", "UNKNOWN", "NOT_APPLICABLE"}:
            continue

        haystack = _text(record)
        if normalized:
            if haystack == normalized:
                match_rank = 0
            elif normalized in haystack:
                match_rank = 1
            elif tokens and all(token in haystack for token in tokens):
                match_rank = 2
            elif any(token in haystack for token in tokens):
                match_rank = 3
            else:
                continue
        else:
            match_rank = 4

        freshness_rank = {"FRESH": 0, "AGED": 1}.get(state, 2)
        type_rank = TYPE_PRIORITY.get(record["memory_type"], 99)
        result.append(
            (
                (
                    match_rank,
                    type_rank,
                    freshness_rank,
                    -float(record["confidence"]),
                    record["memory_id"],
                ),
                {**record, "freshness_state": state},
            )
        )

    result.sort(key=lambda pair: pair[0])
    return [record for _, record in result[:limit]]


def build_context(
    records: Iterable[dict[str, Any]],
    *,
    query: str | None = None,
    max_records: int = 100,
    max_utf8_bytes: int = 256_000,
    now: datetime | None = None,
    **filters: Any,
) -> dict[str, Any]:
    if not 0 <= max_records <= 500:
        raise ValueError("max_records must be between 0 and 500")
    if max_utf8_bytes < 0:
        raise ValueError("budgets must be non-negative")

    matches = search(
        records,
        query=query,
        limit=max(1, min(500, max_records or 1)),
        now=now,
        **filters,
    )
    selected: list[dict[str, Any]] = []
    used = 0

    for record in matches:
        if len(selected) >= max_records:
            break
        payload = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if used + len(payload) > max_utf8_bytes:
            break
        selected.append(record)
        used += len(payload)

    context = {
        "context_protocol": "jarvis-memory-context-v1",
        "items": selected,
        "budget": {
            "max_records": max_records,
            "max_utf8_bytes": max_utf8_bytes,
            "used_records": len(selected),
            "used_utf8_bytes": used,
        },
        "untrusted_data": True,
    }
    return {**context, "revision": digest_json(context)}
