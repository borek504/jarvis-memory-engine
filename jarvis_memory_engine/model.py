from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping

from .canonical import canonical_bytes, parse_utc


MAX_CONTENT_BYTES = 256 * 1024

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
        limits = {
            "domain": 64,
            "subject_type": 64,
            "subject_id": 256,
            "slot_key": 128,
        }
        for name, limit in limits.items():
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if len(value.strip()) > limit:
                raise ValueError(f"{name} exceeds {limit} characters")
        if self.project_scope is not None and (
            not isinstance(self.project_scope, str) or not self.project_scope.strip()
        ):
            raise ValueError("project_scope must be None or a non-empty string")
        if self.project_scope is not None and len(self.project_scope.strip()) > 128:
            raise ValueError("project_scope exceeds 128 characters")


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
        if len(canonical_bytes(dict(self.content))) > MAX_CONTENT_BYTES:
            raise ValueError("content exceeds 256 KiB")
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            raise ValueError("source_kind must be a non-empty string")
        if len(self.source_kind.strip()) > 64:
            raise ValueError("source_kind exceeds 64 characters")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("source_ref must be a non-empty string")
        if len(self.source_ref.strip()) > 2048:
            raise ValueError("source_ref exceeds 2048 characters")
        if self.freshness_class not in FRESHNESS_CLASSES:
            raise ValueError("invalid freshness_class")
        if self.freshness_class == "HISTORICAL":
            raise ValueError("head-bearing memories cannot use HISTORICAL freshness")
        if self.sensitivity not in SENSITIVITY_CLASSES:
            raise ValueError("invalid sensitivity")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= float(self.confidence) <= 1
        ):
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
        if self.freshness_class == "TIME_BOUND" and not all(
            (self.observed_at_utc, self.aged_after_utc, self.stale_after_utc)
        ):
            raise ValueError(
                "TIME_BOUND requires observed_at_utc, aged_after_utc, and stale_after_utc"
            )
        if self.aged_after_utc and self.stale_after_utc:
            if parse_utc(self.stale_after_utc) < parse_utc(self.aged_after_utc):
                raise ValueError("stale_after_utc cannot precede aged_after_utc")
        if self.observed_at_utc and self.aged_after_utc:
            if parse_utc(self.aged_after_utc) < parse_utc(self.observed_at_utc):
                raise ValueError("aged_after_utc cannot precede observed_at_utc")
        if self.source_digest is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.source_digest
        ):
            raise ValueError("source_digest must be a lowercase SHA-256 hex digest")


def freshness_state(
    *,
    freshness_class: str,
    lifecycle: str,
    aged_after_utc: str | None,
    stale_after_utc: str | None,
    validated_at_utc: str | None,
    now: datetime | None = None,
) -> str:
    if freshness_class not in FRESHNESS_CLASSES:
        raise ValueError("invalid freshness_class")
    if lifecycle not in LIFECYCLE_STATES:
        raise ValueError("invalid lifecycle")
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
