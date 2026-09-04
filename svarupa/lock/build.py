"""Graph to lockfile records: the committed half of the output.

This is the narrowest stage in the system, deliberately. Everything else asks
"what can we say about this code"; this asks "what would a reviewer want to see
appear as a green line in a pull request". The answer is a small, stable set of
facts, and the value comes from what is left out.

Three constraints, each from a promoted decision:

* **No line numbers, ever.** Line numbers are the most volatile data here: any
  edit above a record shifts them and churns a committed file, which
  contradicts the stability the format exists for. Evidence lives in
  `graph.json`, which is regenerated.
* **No community identity.** Communities are chaotically sensitive to input
  perturbation, so a lockfile keyed on them would churn on every pull request.
  Module identity comes from directories, packages and workspace members, which
  is what developers declare. A test asserts no community anchor reaches the
  output.
* **Collisions diagnose, never merge.** Two module keys that differ on disk but
  coincide after NFC normalization, or after case folding, are reported rather
  than silently collapsed into one fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from svarupa.build import Graph
from svarupa.diagnostics import Diagnostic
from svarupa.extract import GRAMMAR_VERSIONS
from svarupa.identity import collision_check
from svarupa.lock.grammar import Lockfile, Record, dep_record, module_record

__all__ = ["LockResult", "build_lock", "lock_records"]


@dataclass(frozen=True, slots=True)
class LockResult:
    lockfile: Lockfile
    diagnostics: tuple[Diagnostic, ...]


def lock_records(graph: Graph) -> tuple[Record, ...]:
    """The architectural facts, and nothing else.

    Modules and their dependencies in P1. `endpoint`, `datastore`, `service`,
    `queue` and `surface` are published in the grammar with their arities so
    that adding them in P2 is an additive schema bump rather than a flag day,
    but nothing emits them yet and this function does not pretend otherwise.

    `module_deps` is used rather than the file-level edges: it is already the
    deduplicated module-to-module relation, which is exactly the granularity a
    reviewer reads. Deriving it again here would be a second implementation
    that can disagree with the first.
    """
    records: list[Record] = [module_record(m) for m in sorted(graph.modules)]
    records.extend(dep_record(src, dst) for src, dst in sorted(graph.module_deps))
    return tuple(records)


def build_lock(graph: Graph, tool_version: str) -> LockResult:
    """Assemble the lockfile, with collision diagnostics attached.

    Grammar versions go in the header because a grammar patch release can
    change node structure and therefore extraction output. Without them, a
    lockfile diff after a dependency bump is indistinguishable from a diff
    caused by the code changing, and the reviewer has no way to tell.

    Only grammars actually used by this scan are recorded. Stamping every
    grammar the tool ships with would make the header churn when an unrelated
    language extractor is added, which is the same churn the format exists to
    avoid.
    """
    langs = {lang for lang, _count in graph.file_languages}
    grammars = {
        lang: version for lang, version in sorted(GRAMMAR_VERSIONS.items()) if lang in langs
    }
    diagnostics = tuple(collision_check(sorted(graph.modules)))
    return LockResult(Lockfile.build(tool_version, grammars, lock_records(graph)), diagnostics)
