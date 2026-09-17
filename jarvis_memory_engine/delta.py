from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_bytes, digest_json, parse_utc

PROTOCOL = "jarvis-memory-delta-v1"
MAX_BYTES = 64 * 1024
MAX_CANDIDATES = 32
ACTIONS = {"CREATE", "CORRECT", "RETRACT", "REFERENCE_ONLY", "REVIEW_REQUIRED"}
SECRET_KEYS_NORMALIZED = {
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "privatekey",
    "clientsecret",
    "refreshtoken",
    "accesstoken",
}
PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKEN_RE = re.compile(
    r"(?:\bgh[pousr]_[A-Za-z0-9_]{20,}\b|\bAIza[0-9A-Za-z_-]{20,}\b|\bya29\.[0-9A-Za-z._-]{20,}\b)"
)


class DeltaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedDelta:
    value: dict[str, Any]
    canonical_bytes: bytes
    sha256: str

    @property
    def filename(self) -> str:
        return f"{self.value['delta_id']}_{self.sha256}.json"


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _scan_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _normalized_key(key) in SECRET_KEYS_NORMALIZED:
                raise DeltaValidationError(f"secret-like field forbidden at {path}.{key}")
            _scan_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if PEM_RE.search(value):
            raise DeltaValidationError(f"private key material forbidden at {path}")
        if TOKEN_RE.search(value):
            raise DeltaValidationError(f"token-like material forbidden at {path}")


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeltaValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_delta_bytes(raw: bytes) -> ValidatedDelta:
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_BYTES:
        raise DeltaValidationError("delta exceeds 64 KiB")

    try:
        text = bytes(raw).decode("utf-8")
        value = json.loads(text, object_pairs_hook=_object_no_duplicates)
    except Exception as exc:
        raise DeltaValidationError("invalid UTF-8 JSON") from exc

    if not isinstance(value, dict):
        raise DeltaValidationError("delta root must be an object")

    expected = {"protocol_version", "delta_id", "created_at_utc", "domain", "project_scope", "candidates"}
    if set(value) != expected:
        raise DeltaValidationError("delta root fields are not exact")
    if value["protocol_version"] != PROTOCOL:
        raise DeltaValidationError("invalid protocol_version")
    if not re.fullmatch(r"delta_[0-9a-f]{32}", value["delta_id"]):
        raise DeltaValidationError("invalid delta_id")
    parse_utc(value["created_at_utc"])
    if not isinstance(value["domain"], str) or not value["domain"].strip():
        raise DeltaValidationError("domain must be non-empty")
    if not isinstance(value["project_scope"], str) or not value["project_scope"].strip():
        raise DeltaValidationError("project_scope must be non-empty")

    candidates = value["candidates"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise DeltaValidationError("candidate count must be between 1 and 32")

    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "action", "request"}:
            raise DeltaValidationError("candidate fields are not exact")
        if not re.fullmatch(r"cand_[0-9a-f]{32}", candidate["candidate_id"]):
            raise DeltaValidationError("invalid candidate_id")
        if candidate["candidate_id"] in seen:
            raise DeltaValidationError("duplicate candidate_id")
        seen.add(candidate["candidate_id"])
        if candidate["action"] not in ACTIONS:
            raise DeltaValidationError("invalid action")
        if not isinstance(candidate["request"], dict):
            raise DeltaValidationError("request must be an object")

        request = candidate["request"]
        if "domain" in request and request["domain"] != value["domain"]:
            raise DeltaValidationError("cross-domain candidate forbidden")
        if "project_scope" in request and request["project_scope"] != value["project_scope"]:
            raise DeltaValidationError("cross-project candidate forbidden")
        forbidden_authority = {
            "actor",
            "write_authority",
            "authority",
            "policy_authority",
        }
        if forbidden_authority.intersection(request):
            raise DeltaValidationError("caller-supplied authority fields forbidden")

    _scan_secrets(value)

    payload = canonical_bytes(value)
    return ValidatedDelta(
        value=value,
        canonical_bytes=payload,
        sha256=digest_json(value),
    )
