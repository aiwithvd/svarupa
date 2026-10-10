"""`.svarupa/design.yaml`: the accepted target design, committed by the team."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from svarupa.design.model import Design

__all__ = [
    "DESIGN_FILE",
    "DesignException",
    "DesignFile",
    "UnitSpec",
    "dump_design",
    "load_design",
]

DESIGN_FILE = ".svarupa/design.yaml"


@dataclass(frozen=True)
class UnitSpec:
    style: str
    parts: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


@dataclass(frozen=True)
class DesignException:
    rule: str
    src: str
    dst: str
    reason: str


@dataclass(frozen=True)
class DesignFile:
    units: dict[str, UnitSpec]
    exceptions: list[DesignException]


def load_design(path: Path) -> tuple[DesignFile | None, str]:
    """The accepted design, or None with the reason it could not be used."""
    if not path.is_file():
        return None, ""
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf8"))
    except (OSError, yaml.YAMLError) as exc:
        return (
            None,
            f"{path.name} could not be read ({type(exc).__name__}); using the inferred design",
        )
    if not isinstance(loaded, dict):
        return None, f"{path.name} must be a mapping with `units`; using the inferred design"
    data = cast("dict[str, object]", loaded)
    raw_units: object = data.get("units") or {}
    if not isinstance(raw_units, dict):
        return None, f"{path.name} must map `units` to unit settings; using the inferred design"
    units: dict[str, UnitSpec] = {}
    for unit, spec in cast("dict[object, object]", raw_units).items():
        if not isinstance(spec, dict):
            continue
        entry = cast("dict[str, object]", spec)
        style = entry.get("style")
        if not isinstance(style, str):
            continue
        raw_parts: object = entry.get("parts") or {}
        parts: dict[str, list[str]] = {}
        if isinstance(raw_parts, dict):
            for part, modules in cast("dict[object, object]", raw_parts).items():
                if isinstance(modules, list):
                    parts[str(part)] = [str(m) for m in cast("list[object]", modules)]
        units[str(unit)] = UnitSpec(style, parts)
    exceptions: list[DesignException] = []
    raw_exceptions: object = data.get("exceptions") or []
    if isinstance(raw_exceptions, list):
        for item in cast("list[object]", raw_exceptions):
            if not isinstance(item, dict):
                continue
            e = cast("dict[str, object]", item)
            if e.get("reason"):
                exceptions.append(
                    DesignException(
                        str(e.get("rule", "")),
                        str(e.get("from", "")),
                        str(e.get("to", "")),
                        str(e.get("reason", "")),
                    )
                )
    return DesignFile(units, exceptions), ""


def dump_design(
    design: Design, overrides: dict[str, str], keep: DesignFile | None = None
) -> str:
    """design.yaml for every unit with a chosen (or overridden) style."""
    from svarupa.design import BY_ID

    units: dict[str, object] = {}
    for u in design.units:
        key = u.unit.id or "."
        style_id = overrides.get(key) or (u.chosen.style if u.chosen else None)
        if style_id is None or style_id not in BY_ID:
            continue
        if u.chosen is not None and u.chosen.style == style_id:
            assignments = u.chosen.assignments
        else:
            fit = next((f for f in u.runners_up if f.style == style_id), None)
            assignments = fit.assignments if fit else ()
        parts: dict[str, list[str]] = {}
        for a in assignments:
            parts.setdefault(a.part, []).append(a.module)
        units[key] = {
            "style": style_id,
            "parts": {p: sorted(ms) for p, ms in sorted(parts.items())},
        }
    exceptions = [
        {"rule": e.rule, "from": e.src, "to": e.dst, "reason": e.reason}
        for e in (keep.exceptions if keep else [])
    ]
    header = (
        "# The target design for this repository, accepted with `svarupa design`.\n"
        "# Edit parts and add exceptions (each needs a reason); commit this file.\n"
    )
    return header + yaml.safe_dump(
        {"units": units, "exceptions": exceptions}, sort_keys=False, allow_unicode=True
    )
