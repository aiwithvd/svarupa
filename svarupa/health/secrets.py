"""Committed secrets: known key formats and secret-named assignments.

The value is never kept: a violation carries the rule, the line and the
first 12 hex characters of the value's SHA-256, enough to tell two findings
apart and to check a rotated key, and useless to anyone reading the report.
Formats follow the gitleaks default rules.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from collections.abc import Mapping

from svarupa.detect import FileRole, Scan, read_text
from svarupa.extract.base import module_of
from svarupa.health.catalog import BY_ID
from svarupa.health.model import Violation
from svarupa.model import Evidence

__all__ = ["secret_checks", "secret_texts"]

# fmt: off
_KEYS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{82})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{24,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[0-9A-Za-z_-]{32,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{40,}")),
    ("Private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[A-Za-z0-9+/=\s]{0,64}")),
    ("JSON Web Token", re.compile(r"\beyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}")),
)
# fmt: on
_NAME = (
    r"[\w.-]*?(?:password|passwd|pwd|secret|secret[_-]?key|api[_-]?key|access[_-]?key"
    r"|auth[_-]?token|access[_-]?token|private[_-]?key|client[_-]?secret|token)"
)
# Characters a key or password is made of; a value with spaces, pipes or
# brackets is a sentence or a pattern, not a secret.
_VALUE = r"[A-Za-z0-9_\-+/=.@!%*^~]{8,200}"
_QUOTED = re.compile(rf"(?i)(?<![\w.-])({_NAME})[\"']?\s*(?:=|:|=>|:=)\s*([\"'])({_VALUE})\2")
_BARE = re.compile(
    rf"(?im)^[ \t]*(?:export[ \t]+)?({_NAME})[ \t]*[=:][ \t]*({_VALUE})[ \t]*(?:#.*)?$"
)
_CONFIG = (".env", ".properties", ".ini", ".cfg", ".conf", ".yaml", ".yml")
_PLACEHOLDER = re.compile(
    r"(?i)change.?me|example|sample|dummy|placeholder|your|xxx|\*\*\*|redacted|fake|test"
    r"|todo|^none$|^null$|^undefined$|^<|^\$|^\{\{|^%\(|environ|getenv"
)
_SKIP_NAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "go.sum",
        "Cargo.lock",
    }
)
_MIN_ENTROPY = 3.0
_MAX_CHARS = 1_000_000


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _is_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(".env") or name.endswith(_CONFIG)


def secret_texts(scan: Scan) -> dict[str, str]:
    """Every file a secret could be committed in: tracked source and config of
    any role but generated or vendored, plus files of no analyzed language."""
    paths = [
        f.path for f in scan.files if f.role not in (FileRole.GENERATED, FileRole.VENDORED)
    ] + list(scan.others)
    out: dict[str, str] = {}
    for path in sorted(set(paths)):
        if path.startswith(".svarupa/") or path.rsplit("/", 1)[-1] in _SKIP_NAMES:
            continue
        try:
            text = read_text(scan.root, path)
        except OSError:
            continue
        if len(text) <= _MAX_CHARS and "\x00" not in text:
            out[path] = text
    return out


def _found(path: str, text: str) -> list[tuple[int, str, str]]:
    """(offset, rule, value) for each secret in one file."""
    hits: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for rule, rx in _KEYS:
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            if _PLACEHOLDER.search(m.group(0)):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), rule, m.group(0)))
    patterns = [(_QUOTED, 3)] + ([(_BARE, 2)] if _is_config(path) else [])
    for rx, group in patterns:
        for m in rx.finditer(text):
            value = m.group(group)
            start = m.start(group)
            if any(a <= start < b for a, b in taken):
                continue
            if _PLACEHOLDER.search(value) or _entropy(value) < _MIN_ENTROPY:
                continue
            taken.append((start, m.end(group)))
            hits.append((start, f"{m.group(1)} assigned a literal", value))
    return sorted(hits)


def secret_checks(texts: Mapping[str, str]) -> list[Violation]:
    check = BY_ID["hardcoded-secret"]
    out: list[Violation] = []
    for path, text in sorted(texts.items()):
        newlines = [i for i, c in enumerate(text) if c == "\n"]
        for offset, rule, value in _found(path, text):
            line = bisect.bisect_left(newlines, offset) + 1
            fingerprint = hashlib.sha256(value.encode()).hexdigest()[:12]
            out.append(
                Violation(
                    check="hardcoded-secret",
                    message=(
                        f"{rule} (fingerprint {fingerprint}); move it to a secret store "
                        "and rotate it (CWE-798)"
                    ),
                    evidence=(Evidence(path, line, line),),
                    module=module_of(path),
                    value=1,
                    minutes=check.minutes,
                )
            )
    return out
