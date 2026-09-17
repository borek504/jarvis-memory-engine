from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys


FORBIDDEN_SUFFIXES = {
    ".db",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_NAMES = {
    ".env",
    "credentials.json",
    "service-account.json",
}
IGNORED_PARTS = {".git", ".venv", "__pycache__", "build", "dist"}


def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    account_handle = "borek" + "504"
    client_mail_key = "client" + "_email"
    return (
        ("macOS absolute home path", re.compile("/" + r"Users/[^/\s\"']+")),
        ("Linux absolute home path", re.compile("/" + r"home/[^/\s\"']+")),
        (
            "Windows absolute home path",
            re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\s\"']+", re.IGNORECASE),
        ),
        (
            "e-mail address",
            re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        ),
        (
            "private key material",
            re.compile(r"-----BEGIN [A-Z ]*" + "PRIVATE" + r" KEY-----"),
        ),
        (
            "token-like material",
            re.compile(
                r"(?:\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
                r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b|"
                r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b|"
                r"\bglpat-[A-Za-z0-9_-]{20,}\b|"
                r"\bAKIA[0-9A-Z]{16}\b|"
                r"\bAIza[0-9A-Za-z_-]{20,}\b|"
                r"\bya29\.[0-9A-Za-z._-]{20,}\b)"
            ),
        ),
        ("private account handle", re.compile(re.escape(account_handle), re.IGNORECASE)),
        (
            "service-account field",
            re.compile(r"[\"']" + re.escape(client_mail_key) + r"[\"']\s*:"),
        ),
    )


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str


def _candidate_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(
            part in IGNORED_PARTS or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        yield path, relative


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    patterns = _patterns()
    for path, relative in _candidate_files(root):
        lower_name = path.name.casefold()
        if lower_name in FORBIDDEN_NAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(Finding(relative.as_posix(), "forbidden file type"))
            continue
        raw = path.read_bytes()
        if b"\x00" in raw:
            findings.append(Finding(relative.as_posix(), "unexpected binary file"))
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(relative.as_posix(), "non-UTF-8 file"))
            continue
        for label, pattern in patterns:
            if pattern.search(text):
                findings.append(Finding(relative.as_posix(), label))
    return findings


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    findings = scan_tree(root)
    if findings:
        for finding in findings:
            print(f"PRIVACY_FINDING={finding.rule}:{finding.path}")
        print(f"PRIVACY_SCAN=FAIL findings={len(findings)}")
        return 1
    count = sum(1 for _ in _candidate_files(root))
    print(f"PRIVACY_SCAN=PASS files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
