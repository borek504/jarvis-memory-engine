from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .canonical import parse_utc

MEMORY_TYPES = {
    "FACT",
    "PREFERENCE",
    "GOAL",
    "RULE",
    "DECISION",
    "PROJECT_STATE",
    "EVENT",
    "REFERENCE",
}
HEAD_BEARING_TYPES = MEMORY_TYPES - {"EVENT"}
LIFECYCLE_STATES = {"CURRENT", "SUPERSEDED", "RETRACTED", "PURGED"}
FRESHNESS_CLASSES = {"DURABLE", "TIME_BOUND", "SOURCE_BOUND", "HISTORICAL"}
FRESHNESS_STATES = {"FRESH", "AGED", "STALE", "UNKNOWN", "NOT_APPLICABLE"}
SENSITIVITY_CLASSES = {"STANDARD_PRIVATE", "SENSITIVE", "RESTRICTED_LOCAL"}
WRITE_AUTHORITIES = {
    "HUMAN_EXPLICIT",
    "HUMAN_APPROVED_MIGRATION",
    "POLICY_AUTHORIZED_SYNC",
}


@dataclass(frozen=True)
class Scope:
    domain: str
    subject_type: str
    subject_id: str
    slot_key: str
    project_scope: str | None = None

    def validate(self) -> None:
        for name in ("domain", "subject_type", "subject_id", "slot_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.project_scope is not None and (
            not isinstance(self.project_scope, str) or not self.project_scope.strip()
        ):
            raise ValueError("project_scope must be None or a non-empty string")


@dataclass(frozen=True)
class MemoryInput:
    memory_type: str
    scope: Scope
    content: Mapping[str, Any]
    source_kind: str
    source_ref: str
    freshness_class: str = "DURABLE"
    sensitivity: str = "STANDARD_PRIVATE"
    confidence: float = 1.0
    observed_at_utc: str | None = None
    validated_at_utc: str | None = None
    aged_after_utc: str | None = None
    stale_after_utc: str | None = None
    source_digest: str | None = None

    def validate(self) -> None:
        self.scope.validate()
        if self.memory_type not in HEAD_BEARING_TYPES:
            raise ValueError("memory_type must be a head-bearing type")
        if not isinstance(self.content, Mapping):
            raise ValueError("content must be an object")
        if not self.source_kind or not isinstance(self.source_kind, str):
            raise ValueError("source_kind must be a non-empty string")
        if not self.source_ref or not isinstance(self.source_ref, str):
            raise ValueError("source_ref must be a non-empty string")
        if self.freshness_class not in FRESHNESS_CLASSES:
            raise ValueError("invalid freshness_class")
        if self.sensitivity not in SENSITIVITY_CLASSES:
            raise ValueError("invalid sensitivity")
        if not isinstance(self.confidence, (int, float)) or not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for name in (
            "observed_at_utc",
            "validated_at_utc",
            "aged_after_utc",
            "stale_after_utc",
        ):
            value = getattr(self, name)
            if value is not None:
                parse_utc(value)
        if self.aged_after_utc and self.stale_after_utc:
            if parse_utc(self.stale_after_utc) < parse_utc(self.aged_after_utc):
                raise ValueError("stale_after_utc cannot precede aged_after_utc")


def freshness_state(
    *,
    freshness_class: str,
    lifecycle: str,
    aged_after_utc: str | None,
    stale_after_utc: str | None,
    validated_at_utc: str | None,
    now: datetime | None = None,
) -> str:
    if lifecycle != "CURRENT":
        return "NOT_APPLICABLE"

    if freshness_class == "DURABLE":
        return "FRESH"

    if freshness_class == "HISTORICAL":
        return "NOT_APPLICABLE"

    if freshness_class == "SOURCE_BOUND" and validated_at_utc is None:
        return "UNKNOWN"

    evaluated = now or datetime.now(timezone.utc)
    if evaluated.tzinfo is None or evaluated.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    evaluated = evaluated.astimezone(timezone.utc)

    if stale_after_utc and evaluated >= parse_utc(stale_after_utc):
        return "STALE"
    if aged_after_utc and evaluated >= parse_utc(aged_after_utc):
        return "AGED"
    return "FRESH"
