"""Plain explanations of the health checks: what a finding means, why it
matters and how to fix it, with a short before/after per language.

The text is fixed and lives in `explanations/<check-id>.toml` next to this
module, so it is reviewed like code and never generated.
"""

from __future__ import annotations

import importlib
import textwrap
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, cast

__all__ = [
    "LANGS",
    "Example",
    "Explanation",
    "ExplanationError",
    "example_for",
    "explanation",
    "parse",
    "required_languages",
]

LANGS = ("python", "typescript", "go", "java")
_KEYS = frozenset({"what", "why", "fix", "example"})
# A language whose pack shares another pack's rules shares its examples.
_SAME_AS = {"javascript": "typescript"}
_FUNCTION_CHECKS = frozenset(
    {"complex-function", "long-function", "many-parameters", "deep-nesting", "large-class"}
)
_SINK_CHECKS = {"sql-injection": "sql", "command-injection": "shell", "code-injection": "code"}


class ExplanationError(ValueError):
    """An explanation file that cannot be shown as written."""


@dataclass(frozen=True, slots=True)
class Example:
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class Explanation:
    check: str
    what: str
    why: str
    fix: str
    examples: tuple[tuple[str, Example], ...] = ()  # sorted by language

    def to_json(self) -> dict[str, object]:
        return {
            "what": self.what,
            "why": self.why,
            "fix": self.fix,
            "examples": {
                lang: {"before": ex.before, "after": ex.after} for lang, ex in self.examples
            },
        }


def _text(check: str, where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplanationError(f"{check}: {where} is empty")
    return value


def _code(text: str) -> str:
    return textwrap.dedent(text).strip("\n").rstrip()


def parse(check: str, data: dict[str, Any]) -> Explanation:
    """One explanation from its parsed TOML table."""
    unknown = set(data) - _KEYS
    if unknown:
        raise ExplanationError(f"{check}: unknown key {sorted(unknown)[0]!r}")
    examples: list[tuple[str, Example]] = []
    for lang, body in sorted(cast("dict[str, Any]", data.get("example", {})).items()):
        if lang not in LANGS:
            raise ExplanationError(f"{check}: unknown language {lang!r}")
        if not isinstance(body, dict) or set(cast("dict[str, Any]", body)) != {
            "before",
            "after",
        }:
            raise ExplanationError(f"{check}: example.{lang} needs before and after")
        pair = cast("dict[str, Any]", body)
        examples.append(
            (
                lang,
                Example(
                    _code(_text(check, f"example.{lang}.before", pair["before"])),
                    _code(_text(check, f"example.{lang}.after", pair["after"])),
                ),
            )
        )
    return Explanation(
        check,
        _text(check, "what", data.get("what")).strip(),
        _text(check, "why", data.get("why")).strip(),
        _text(check, "fix", data.get("fix")).strip(),
        tuple(examples),
    )


def _loads(text: str) -> dict[str, Any]:
    """tomllib is stdlib from 3.11; tomli is the 3.10 backport (a dependency)."""
    for module in ("tomllib", "tomli"):
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        return cast("dict[str, Any]", mod.loads(text))
    raise ExplanationError("no TOML parser: install tomli on Python 3.10")


@cache
def explanation(check_id: str) -> Explanation:
    path = (
        resources.files("svarupa.health").joinpath("explanations").joinpath(f"{check_id}.toml")
    )
    if not path.is_file():
        raise ExplanationError(f"{check_id}: no explanation file")
    return parse(check_id, _loads(path.read_text(encoding="utf8")))


def example_for(check_id: str, lang: str | None) -> Example | None:
    if lang is None:
        return None
    return dict(explanation(check_id).examples).get(_SAME_AS.get(lang, lang))


def required_languages(check_id: str) -> frozenset[str]:
    """The languages whose pack can produce this check."""
    from svarupa.extract.packs import PACKS

    out: set[str] = set()
    for pack in PACKS:
        lang = _SAME_AS.get(pack.lang, pack.lang)
        if check_id in _FUNCTION_CHECKS and pack.metrics is not None:
            out.add(lang)
        r = pack.risks
        if r is None:
            continue
        if check_id in _SINK_CHECKS and any(s.tag == _SINK_CHECKS[check_id] for s in r.sinks):
            out.add(lang)
        if any(u.check == check_id for u in r.unsafe):
            out.add(lang)
        if (
            (check_id == "query-in-loop" and r.datastore_calls is not None)
            or (check_id == "blocking-in-async" and r.blocking is not None and r.async_marker)
            or (check_id == "swallowed-error" and r.catch_types)
            or (check_id == "endpoint-without-auth" and r.auth_markers)
        ):
            out.add(lang)
    return frozenset(out)
