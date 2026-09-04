"""Identity collisions: ids that differ on disk but coincide as keys.

Its own module because two stages need it and neither owns it. `detect`
computes it, on the **raw** filesystem names, before any normalization has run;
the lockfile is what the answer protects, since two ids collapsing into one key
would make a committed file silently wrong. Living in either stage forced a
circular import, which was the design telling me it belongs to neither.

Pure string logic, no dependencies beyond the standard library, so it can sit
below both.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence

from svarupa.diagnostics import Diagnostic, Severity

__all__ = ["collision_check"]


def collision_check(module_ids: Sequence[str]) -> list[Diagnostic]:
    """Diagnose ids that would merge into one lockfile key.

    Must be given the **raw** ids, before normalization: once `norm_path` has
    run, the pre-images are gone and there is nothing left to compare.

    Two separate axes, reported separately because the remedy differs:

    * **normalization** — NFC and NFD of one name are two distinct files on
      Linux but one file on macOS. `casefold()` alone does not catch this,
      because it does not normalize.
    * **case** — distinct on Linux, one file on a case-insensitive APFS or
      NTFS volume.

    Silently merging either would produce a silently wrong lockfile, which is
    worse than failing, so this returns diagnostics rather than a merged view.
    """
    out: list[Diagnostic] = []

    def bucket(key: Callable[[str], str]) -> dict[str, list[str]]:
        b: dict[str, list[str]] = {}
        for mid in module_ids:
            b.setdefault(key(mid), []).append(mid)
        return b

    def to_nfc(x: str) -> str:
        return unicodedata.normalize("NFC", x)

    def to_nfc_folded(x: str) -> str:
        return unicodedata.normalize("NFC", x).casefold()

    seen: set[tuple[str, ...]] = set()

    for norm_key, group in sorted(bucket(to_nfc).items()):
        uniq = sorted(set(group))
        if len(uniq) > 1:
            seen.add(tuple(uniq))
            out.append(
                Diagnostic(
                    code="SVA-L-004",
                    severity=Severity.ERROR,
                    message=(
                        "these paths are distinct on disk but identical after "
                        "Unicode NFC normalization, so they would collapse into "
                        "one lockfile key"
                    ),
                    subject=" | ".join(uniq),
                    location=norm_key,
                    suggested_fixes=(
                        "Rename one path so the two differ by more than "
                        "Unicode normalization form.",
                    ),
                )
            )

    for fold_key, group in sorted(bucket(to_nfc_folded).items()):
        uniq = sorted(set(group))
        if len(uniq) > 1 and tuple(uniq) not in seen:
            out.append(
                Diagnostic(
                    code="SVA-L-004",
                    severity=Severity.ERROR,
                    message=(
                        "these paths differ only by case, so they are distinct "
                        "on Linux but one file on a case-insensitive volume"
                    ),
                    subject=" | ".join(uniq),
                    location=fold_key,
                    suggested_fixes=("Rename one path so the two differ by more than case.",),
                )
            )
    return out
