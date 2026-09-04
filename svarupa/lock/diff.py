"""The diff engine: what this change did to the architecture.

This is the moat. A diagram is a nice onboarding artifact; a deterministic
architecture *delta* per pull request is infrastructure, and it only works if
the numbers are trustworthy in both directions: every real change appears, and
nothing that is not a change appears.

**Base drift is the failure that matters most**, and it is subtle enough to
deserve the design's own section. The engine compares two lockfiles: the one
committed on the base branch, and the one generated from this head. If the
committed base is stale, out of date with the base branch's own code, then the
difference between it and head includes changes made by *other* people in
*other* commits. A reviewer reads that as "this pull request added a dependency
from billing to auth" when the pull request did nothing of the kind.

Silence there is the worst option: it is a wrong claim about someone's change,
attributed to them by a bot. So drift is detected by regenerating the base
lockfile from the base commit's own code and comparing it to the committed one.
When they disagree, the delta says so, names how many facts drifted, and the
comment must lead with that rather than with the delta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svarupa.diagnostics import Diagnostic, Severity
from svarupa.lock.grammar import Lockfile, Record

__all__ = ["ArchitectureDelta", "diff", "drift_check"]


@dataclass(frozen=True, slots=True)
class ArchitectureDelta:
    """What changed, in the only two directions a set of facts can change.

    Records are facts, not objects with identity, so there is no "modified": a
    changed dependency is one removal and one addition. That is a deliberate
    consequence of one fact per line, and it is what lets `git diff` render an
    architecture change natively.
    """

    added: tuple[Record, ...] = ()
    removed: tuple[Record, ...] = ()
    unknown_kinds: frozenset[str] = frozenset()
    diagnostics: tuple[Diagnostic, ...] = field(default=())

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed

    def by_kind(self, kind: str) -> tuple[tuple[Record, ...], tuple[Record, ...]]:
        return (
            tuple(r for r in self.added if r.kind == kind),
            tuple(r for r in self.removed if r.kind == kind),
        )

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({r.kind for r in (*self.added, *self.removed)}))

    def render(self) -> str:
        """One fact per line, prefixed the way a diff reads.

        Removals before additions within a kind, and kinds in codepoint order,
        so the same delta always renders identically. A summary a reader can
        skim comes first, but the lines are the product.
        """
        if self.empty:
            return "No architectural change."
        out: list[str] = [
            f"{len(self.added)} added, {len(self.removed)} removed "
            f"across {len(self.kinds)} record kind(s)."
        ]
        for kind in self.kinds:
            added, removed = self.by_kind(kind)
            out.append("")
            out.append(f"{kind}:")
            out.extend(f"  - {r.render()}" for r in removed)
            out.extend(f"  + {r.render()}" for r in added)
        if self.unknown_kinds:
            out.append("")
            out.append(
                "Record kinds this build does not understand, diffed opaquely: "
                + ", ".join(sorted(self.unknown_kinds))
            )
        return "\n".join(out)


def diff(base: Lockfile, head: Lockfile) -> ArchitectureDelta:
    """Compare two lockfiles.

    Refuses across a major schema boundary rather than producing a delta whose
    field meanings differ between the two sides. A minor difference is fine by
    construction: unknown kinds are retained verbatim and diff as opaque adds
    and removes, which is what stops a new record type becoming a flag day for
    everyone holding an older lockfile.
    """
    base.assert_diffable(head)

    base_set, head_set = set(base.records), set(head.records)
    added = tuple(sorted(head_set - base_set))
    removed = tuple(sorted(base_set - head_set))
    unknown = (base.unknown_kinds() | head.unknown_kinds()) & {
        r.kind for r in (*added, *removed)
    }

    diagnostics: list[Diagnostic] = []
    if unknown:
        diagnostics.append(
            Diagnostic(
                code="SVA-L-005",
                severity=Severity.INFO,
                message=(
                    "the delta contains record kinds this build does not understand; "
                    "they are diffed as opaque lines, which is correct but means the "
                    "summary cannot describe what they mean"
                ),
                subject=", ".join(sorted(unknown)),
                suggested_fixes=("Upgrade svarupa to a build that knows these kinds.",),
            )
        )
    return ArchitectureDelta(added, removed, frozenset(unknown), tuple(diagnostics))


def drift_check(committed_base: Lockfile, regenerated_base: Lockfile) -> tuple[Diagnostic, ...]:
    """Has the committed base lockfile fallen out of date with base-branch code?

    Both arguments describe the *same* commit: one is what someone committed,
    the other is what the code there actually produces. Any difference is
    drift, and drift is not a neutral inaccuracy. Every drifted fact shows up
    in the head delta as though this change caused it, so a bot would tell an
    author they added a dependency that was already there.

    Returns diagnostics rather than raising. The delta is still worth showing;
    what changes is that the drift has to be stated first, because otherwise
    the numbers below it are wrong in a way the reader cannot see.
    """
    delta = diff(committed_base, regenerated_base)
    if delta.empty:
        return ()
    return (
        Diagnostic(
            code="SVA-L-006",
            severity=Severity.WARNING,
            message=(
                f"the committed base lockfile is out of date with the code at the same "
                f"commit: {len(delta.added)} fact(s) missing from it and "
                f"{len(delta.removed)} fact(s) it still claims. Those "
                f"{len(delta.added) + len(delta.removed)} difference(s) will appear in "
                "the delta below as though this change caused them"
            ),
            subject="architecture.lock",
            suggested_fixes=(
                "Regenerate the lockfile on the base branch and commit it.",
                "Until then, read the delta as base-drift plus this change, not as "
                "this change alone.",
            ),
        ),
    )
