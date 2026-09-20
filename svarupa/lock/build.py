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

from svarupa.build import Graph, entrypoint_module, module_of, module_roles
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract import GRAMMAR_VERSIONS
from svarupa.identity import collision_check
from svarupa.lock.grammar import Lockfile, Record, dep_record, module_record
from svarupa.model import NodeKind

_SYSTEM_KINDS = (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE)

__all__ = [
    "ROOT_MODULE",
    "LockResult",
    "build_lock",
    "code_modules",
    "lock_records",
    "spell",
]

# How the repository root is spelled in the lockfile. Its module id is the
# empty string, which rendered as `module` followed by a bare tab: a line whose
# entire meaning is trailing whitespace.
#
# That is not a parser problem, it is an environment problem. Nearly every
# repository has architecture files at its root, so nearly every committed
# lockfile would carry such a line, and pre-commit's `trailing-whitespace` hook
# and every editor's trim-on-save delete it silently. Measured: after that
# trim, the file refuses to parse with SVA-L-002 and every CI diff fails until
# someone regenerates.
#
# `.` cannot collide with a real module id, because no directory inside a
# repository is named `.`. Changed now rather than later: after adoption this
# would be a major schema bump.
ROOT_MODULE = "."


@dataclass(frozen=True, slots=True)
class LockResult:
    lockfile: Lockfile
    diagnostics: tuple[Diagnostic, ...]


def lock_records(graph: Graph) -> tuple[Record, ...]:
    """The architectural facts, and nothing else.

    Modules, their dependencies, and the deployment facts: services,
    datastores and queues from compose. Their kinds and arities were published
    in the grammar from the start, so emitting them now is the additive
    evolution the schema policy was designed for: an older build diffs the new
    lines as opaque adds, never as a flag day. A new service appearing in a
    pull request is exactly the green line a reviewer wants. The same holds
    for the semantic kinds added since — `endpoint`, `entrypoint`, `role`, and
    `environment` (the declared deploy targets, names only). `surface` stays
    published-but-unemitted.

    `module_deps` is used rather than the file-level edges: it is already the
    deduplicated module-to-module relation, which is exactly the granularity a
    reviewer reads. Deriving it again here would be a second implementation
    that can disagree with the first.
    """
    records: list[Record] = [module_record(spell(m)) for m in sorted(code_modules(graph))]
    records.extend(dep_record(spell(src), spell(dst)) for src, dst in sorted(graph.module_deps))

    kind_map = {
        NodeKind.SERVICE: "service",
        NodeKind.DATASTORE: "datastore",
        NodeKind.QUEUE: "queue",
    }
    records.extend(
        sorted(
            Record(kind_map[node.kind], (node.label,))
            for node in graph.nodes.values()
            if node.kind in kind_map
        )
    )

    # Semantic records. Routes and tasks are gated on architecture
    # eligibility, so a route declared in a test file changes nothing here.
    # Endpoint records carry the handler's MODULE, not its function name:
    # renaming a handler is an intra-module refactor and must churn zero
    # lines, while moving it between modules is architecture and must show.
    modules = code_modules(graph)
    arch_routes = [r for r in graph.routes if r.file in graph.architecture_paths]
    records.extend(
        sorted(
            {
                Record("endpoint", (f"{r.method} {r.path}", spell(module_of(r.file))))
                for r in arch_routes
            }
        )
    )

    # New kinds append as their own sorted blocks after the existing ones.
    # Re-sorting the whole list would reorder every adopted lockfile on
    # upgrade, which is a full-file churn wearing a schema bump's clothes.
    eps: set[Record] = set()
    for e in graph.entrypoints:
        target_module = entrypoint_module(graph, e.target, e.lang, e.file)
        if target_module is not None and target_module in modules:
            eps.add(Record("entrypoint", (e.name, spell(target_module))))
    records.extend(sorted(eps))
    records.extend(
        sorted(
            Record("role", (spell(m), role))
            for m, role_names in module_roles(graph).items()
            if m in modules
            for role in role_names
        )
    )
    # One record per declared canonical environment: the name only. Sources,
    # refs and paths are evidence, and evidence lives in graph.json — a
    # workflow renamed or a values file moved is not an architecture change
    # and must not read as one. Unmatched tokens are recorded verbatim, the
    # same keep-don't-invent rule the extractor follows.
    records.extend(
        sorted({Record("environment", (f.name,)) for f in graph.environments})
    )
    return tuple(records)


def code_modules(graph: Graph) -> set[str]:
    """Modules holding at least one architecture-eligible file in a language
    this build extracts.

    A directory of YAML is not a module, it is configuration. Nothing in it can
    ever produce a `dep`, so a `module` line for it is a fact that cannot
    participate in the structure the lockfile exists to describe, and it is
    pure churn surface: the first workflow directory or the first Sphinx
    `docs/` arrives as an architectural change. Measured on a directory of
    unrelated projects, the unfiltered set was 1,518 module lines, which is not
    the "small and stable" the design promises.

    Configuration enters the lockfile through the record kinds built for it,
    `datastore`, `endpoint`, `service` and `queue`, which the grammar already
    publishes. When the SQL extractor lands, `schema/` returns as a module, and
    that arrival genuinely is a change in what the tool can see, which the
    header's grammar versions let a reviewer attribute correctly.
    """
    out: set[str] = set()
    for nid, node in graph.nodes.items():
        path = nid.split("#", 1)[0]
        # `node.lang in GRAMMAR_VERSIONS`, never truthiness. The filter used to
        # be `if node.lang`, which is a claim about every value any future
        # producer will put in that field, and the compose extractor promptly
        # falsified it: service nodes carry lang="compose", so a directory
        # holding only a docker-compose file was a "module with extractable
        # source" and the config-directory churn this function exists to
        # prevent came straight back, one wave after it was fixed. A guard
        # over an open-ended field enumerates what it accepts.
        if node.lang in GRAMMAR_VERSIONS and path in graph.architecture_paths:
            out.add(path.rsplit("/", 1)[0] if "/" in path else "")
    return out & set(graph.modules)


def spell(module_id: str) -> str:
    """The lockfile spelling of a module id.

    A format concern, not a graph concern: the graph is right to call the root
    `""`, and the committed file needs a name that survives a text editor.
    """
    return module_id or ROOT_MODULE


def build_lock(graph: Graph, tool_version: str) -> LockResult:
    """Assemble the lockfile, with collision diagnostics attached.

    Grammar versions go in the header because a grammar patch release can
    change node structure and therefore extraction output. Without them, a
    lockfile diff after a dependency bump is indistinguishable from a diff
    caused by the code changing, and the reviewer has no way to tell.

    Only grammars that could have contributed a fact are recorded, which is a
    narrower set than "languages this scan saw". Keyed on the wider set, adding
    a single TypeScript **test** file to a pure-Python repository changed the
    committed lockfile while changing zero records, because
    `graph.file_languages` counts every scanned file including the test,
    generated and vendored roles that are excluded from every record.

    Every byte of a committed artifact has to be a function of the facts it
    commits. Keying any part of it, even a header, to inputs excluded from
    those facts reopens the churn channel through the exclusion itself.
    """
    langs = {
        node.lang
        for nid, node in graph.nodes.items()
        if node.lang and nid.split("#", 1)[0] in graph.architecture_paths
    }
    grammars = {
        lang: version for lang, version in sorted(GRAMMAR_VERSIONS.items()) if lang in langs
    }
    records = lock_records(graph)
    diagnostics = list(collision_check(sorted(code_modules(graph))))

    # Two services with the same name in different compose files are two graph
    # nodes and would be ONE lockfile record, because records are facts and the
    # set-dedup collapses them. Deleting one of the two would then produce a
    # zero-line diff, which is exactly the missed line the format exists to
    # show. Collisions diagnose, never merge; that decision was applied to
    # module keys and has to hold for every record kind added later.
    by_label: dict[tuple[str, str], list[str]] = {}
    for nid, node in graph.nodes.items():
        if node.kind in _SYSTEM_KINDS:
            by_label.setdefault((node.kind.value, node.label), []).append(nid)
    for (kind_name, label), holders in sorted(by_label.items()):
        if len(holders) > 1:
            diagnostics.append(
                Diagnostic(
                    code="SVA-L-004",
                    severity=Severity.WARNING,
                    message=(
                        f"{len(holders)} {kind_name} definitions share the name "
                        f"{label!r}, so they collapse into one lockfile record and "
                        "removing one of them will not show in the diff"
                    ),
                    subject=" | ".join(sorted(holders)),
                )
            )

    # An entrypoint whose target resolves to nothing in the graph produces no
    # record; saying so is this layer's job, since the record was its to emit.
    modules = code_modules(graph)
    for e in graph.entrypoints:
        target_module = entrypoint_module(graph, e.target, e.lang, e.file)
        if target_module is None or target_module not in modules:
            diagnostics.append(
                Diagnostic(
                    code="SVA-L-012",
                    severity=Severity.WARNING,
                    message=(
                        "an entrypoint names a module the lockfile does not "
                        "declare, so no entrypoint record was written for it"
                    ),
                    subject=f"{e.name} -> {e.target}",
                    location=str(e.evidence),
                )
            )

    # A dep whose endpoint has no module line would be a dangling reference in
    # a committed file. It cannot happen today, because a dependency is derived
    # from an import and an import needs code at both ends, but "cannot happen"
    # is what a check is for.
    named = {f for r in records if r.kind == "module" for f in r.fields}
    dangling = sorted({f for r in records if r.kind == "dep" for f in r.fields} - named)
    if dangling:
        diagnostics.append(
            Diagnostic(
                code="SVA-L-009",
                severity=Severity.ERROR,
                message=(
                    "a dependency names a module with no module record, so the "
                    "lockfile would refer to something it does not declare"
                ),
                subject=", ".join(dangling[:5]),
            )
        )

    if not records:
        # An empty lockfile is a claim: "this repository has no architecture".
        # Committed silently, it becomes the base everyone diffs against, and
        # the eventual fix arrives as a giant delta attributed to whoever made
        # it. Every other stage refuses or explains when it has nothing.
        diagnostics.append(
            Diagnostic(
                code="SVA-L-010",
                severity=Severity.WARNING,
                message=(
                    "no architectural facts were found, so this lockfile claims the "
                    "repository has no architecture. Committing it makes that the "
                    "base every future diff is measured against"
                ),
                subject=tool_version,
                suggested_fixes=(
                    "Check the scan root and any .svarupaignore rules.",
                    "If the repository really has no extractable source, this is "
                    "correct and can be committed.",
                ),
            )
        )

    return LockResult(Lockfile.build(tool_version, grammars, records), tuple(diagnostics))
