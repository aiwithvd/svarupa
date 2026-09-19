"""Environment facts: where a deploy target is declared, with the line that
declares it.

Users must be able to distinguish production from staging from qa from
development, and in this system that means evidence-backed facts, never hints.
Four declaration sources are read, each citing its own line:

* **Env-suffixed config filenames** — `values-prod.yaml`, `settings/production.py`.
  The filename IS the env label, so this is the highest-precision signal. It is
  gated twice: the token must appear as a full path segment or a clearly
  delimited affix (so `prod_utils.py` is not a claim), and the file must look
  like configuration (so `development-notes.md` is not either).
* **Profile manifests** — each top-level key under `build` in an `eas.json` is
  literally an environment name. JSON discards positions, so the key's line is
  found by a scanner cross-checked against the parse, the same rule the
  entrypoint extractor follows: a declaration whose line cannot be located
  produces no fact, never a guessed line.
* **CI workflow `environment:` keys** — GitHub jobs (scalar or `{name: ...}`)
  and GitLab jobs. Branch triggers are attributes on the environment
  (`ref: main` says what ships from what ref), never environments themselves.
* **Dockerfile `ENV NODE_ENV=<value>`** — one env only, but declared. `ARG
  NODE_ENV` is a build-time default, not a deploy claim, and a value with a
  `$` in it is dynamic: unknown, not guessed.

Aliases fold case-insensitively onto four canonical names (development, qa,
staging, production); an unrecognized token is kept verbatim so nothing is
lost and nothing invented. `test` is deliberately absent: a test role is a
file role, not a deploy env.

Two notes on where the files come from. Workflow files are already scanned
(`ci-github`/`ci-gitlab` config kinds, TOOLING role — CI is where environments
are declared, so the tooling exclusion from architecture does not apply here).
The other sources (`values-*.yaml`, `eas.json`, `Dockerfile`) are not a known
language or config kind, so stage 1 never records them; this module walks for
them itself, pruning the same excluded directories and applying the same role
classification, because a `values-prod.yaml` under `tests/` declares nothing
about the deployed system. That walk does not consult ignore files — the
documented boundary of this slice.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from svarupa.detect import DEFAULT_EXCLUDES, FileRole, Scan, classify
from svarupa.diagnostics import Diagnostic, Severity
from svarupa.extract.base import EnvironmentFact
from svarupa.model import Evidence

__all__ = ["EnvironmentFacts", "canonical_env", "extract_environments"]

# Alias folding, case-insensitive. `test` is NOT here on purpose: it is a file
# role, not a deploy env, and folding it would relabel every test fixture as
# an environment.
_ALIASES = {
    "dev": "development",
    "local": "development",
    "qa": "qa",
    "stage": "staging",
    "uat": "staging",
    "preprod": "staging",
    "preview": "staging",
    "prod": "production",
    "live": "production",
}

# An env token as a full path segment or a delimited affix: `values-prod.yaml`
# and `settings/production.py` match, `prod_utils.py`'s `prod`... also matches
# the token rule; the config-looking gate below is what keeps such helpers
# out. The two gates work as a pair, and decoys fail at least one.
_ENV_TOKEN = (
    "dev|development|local|qa|staging|stage|uat|preprod|preview|prod|production|live"
)
_ENV_RE = re.compile(rf"(?:^|[./_-])({_ENV_TOKEN})(?:[./_-]|$)", re.IGNORECASE)

_CONFIG_EXTS = frozenset({".yaml", ".yml", ".json", ".toml"})
# A `.py` file only claims an env by name when it sits where settings live.
_SETTINGS_DIRS = frozenset({"settings", "config", "configs", "environments", "overlays"})

# A workflow or manifest is human-written and small; a multi-megabyte one is
# not what its name says. Same posture as the compose cap: refuse by size,
# say so, move on.
_MAX_SOURCE_BYTES = 512 * 1024

_MAX_WALK_DEPTH = 40

# A value containing one of these is computed at runtime; the environment it
# names is unknown, and unknown must not be folded into a claim.
_DYNAMIC = re.compile(r"\$|\{\{")

_DOCKERFILE_RE = re.compile(r"^dockerfile(\..+)?$|^.+\.dockerfile$", re.IGNORECASE)


def canonical_env(token: str) -> str:
    """Fold an alias onto its canonical name; keep unmatched tokens verbatim."""
    return _ALIASES.get(token.lower(), token)


@dataclass(frozen=True, slots=True)
class EnvironmentFacts:
    facts: tuple[EnvironmentFact, ...]
    diagnostics: tuple[Diagnostic, ...]


# --------------------------------------------------------------------------
# Small yaml-node helpers (position-preserving, like compose.py)
# --------------------------------------------------------------------------


def _mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    out: dict[str, yaml.Node] = {}
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and isinstance(key.value, str):
            out[key.value] = value
    return out


def _scalar(node: yaml.Node | None) -> str | None:
    if isinstance(node, yaml.ScalarNode) and isinstance(node.value, str):
        return node.value
    return None


def _line(node: yaml.Node) -> int:
    return int(node.start_mark.line) + 1


def _key_lines(node: yaml.Node | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(node, yaml.MappingNode):
        for key, _value in node.value:
            if isinstance(key, yaml.ScalarNode) and isinstance(key.value, str):
                out[key.value] = _line(key)
    return out


def _depth(node: yaml.Node, level: int = 0) -> int:
    if level > 60:
        return level
    if isinstance(node, yaml.MappingNode):
        return max(
            (_depth(n, level + 1) for pair in node.value for n in pair),
            default=level,
        )
    if isinstance(node, yaml.SequenceNode):
        return max((_depth(v, level + 1) for v in node.value), default=level)
    return level


# --------------------------------------------------------------------------
# The walk: files stage 1 never records
# --------------------------------------------------------------------------


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _is_config_looking(rel: str) -> bool:
    """The second filename gate: only configuration claims an env by name.

    `values-prod.yaml` qualifies by extension; `settings/production.py` by
    living where settings live; `development-notes.md` and a bare
    `prod_utils.py` qualify on neither axis and are decoys.
    """
    parts = rel.split("/")
    ext = Path(parts[-1]).suffix.lower()
    if ext in _CONFIG_EXTS:
        return True
    return ext == ".py" and any(d.lower() in _SETTINGS_DIRS for d in parts[:-1])


def _walk_candidates(root: Path) -> list[tuple[str, bytes]]:
    """(rel path, bytes) for every environment-source file stage 1 skipped.

    Deterministic by construction: entries are sorted by codepoint before
    recursion, exactly as detect does, because this module's output inherits
    the same byte-identity contract. Symlinked directories are not followed;
    a symlinked file is read only when it resolves inside the root, the same
    boundary detect draws.
    """
    out: list[tuple[str, bytes]] = []
    real_root = os.path.realpath(root)

    def visit(folder: Path, rel: str, depth: int) -> None:
        if depth > _MAX_WALK_DEPTH:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            name = entry.name
            child_rel = f"{rel}/{name}" if rel else name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name in DEFAULT_EXCLUDES or name.startswith("."):
                        continue
                    visit(Path(entry.path), child_rel, depth + 1)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    # A symlinked file is followed only while it stays inside
                    # the repository; one pointing outside is not this repo's
                    # declaration to read.
                    if not entry.is_symlink():
                        continue
                    target = os.path.realpath(entry.path)
                    if not (target == real_root or target.startswith(real_root + os.sep)):
                        continue
            except OSError:
                continue
            if not _wanted(name, child_rel):
                continue
            try:
                data = Path(entry.path).read_bytes()
            except OSError:
                continue
            if len(data) > _MAX_SOURCE_BYTES:
                continue
            out.append((child_rel, data))

    visit(root, "", 0)
    out.sort(key=lambda item: item[0])
    return out


def _wanted(name: str, rel: str) -> bool:
    return (
        name == "eas.json"
        or _DOCKERFILE_RE.match(name) is not None
        or (_ENV_RE.search(rel) is not None and _is_config_looking(rel))
    )


def _eligible_role(rel: str) -> bool:
    """The same gate semantics applies to manifests: test, generated and
    vendored files declare nothing about the production architecture."""
    return classify(rel, b"", None, None).in_architecture


def _file_evidence(rel: str, data: bytes) -> Evidence:
    """A filename claim cites the file itself: line 1, or `(0, 0)` for an
    empty file, the only place a whole-file citation is legal."""
    n = _line_count(data)
    return Evidence(rel, 1, 1) if n else Evidence(rel, 0, 0)


def _unparseable(path: str, what: str, exc: Exception | None = None) -> Diagnostic:
    suffix = f" ({type(exc).__name__})" if exc is not None else ""
    return Diagnostic(
        code="SVA-X-010",
        severity=Severity.WARNING,
        message=(
            f"{what} could not be parsed or read{suffix}, so its environment "
            "facts are missing"
        ),
        subject=path,
    )


# --------------------------------------------------------------------------
# Source 1: env-suffixed config filenames
# --------------------------------------------------------------------------


def _filename_facts(rel: str, data: bytes) -> list[EnvironmentFact]:
    out: list[EnvironmentFact] = []
    seen: set[str] = set()
    for m in _ENV_RE.finditer(rel):
        token = m.group(1).lower()
        if token in seen:
            continue
        seen.add(token)
        out.append(
            EnvironmentFact(
                name=canonical_env(m.group(1)),
                source="filename",
                evidence=(_file_evidence(rel, data),),
                attrs=(("path", rel),),
            )
        )
    return out


# --------------------------------------------------------------------------
# Source 2: profile manifests (eas.json `build` keys)
# --------------------------------------------------------------------------


def _locate_profile_line(lines: list[str], name: str) -> int | None:
    """The 1-based line of the profile key, scoped to the `build` object.

    The parse established that the key exists under `build`; this scanner only
    locates it. Scoping matters: an eas.json can repeat a profile name under
    `submit`, and the first textual hit would then cite the wrong claim.
    """
    start = next((i for i, ln in enumerate(lines, start=1) if '"build"' in ln), None)
    if start is None:
        return None
    depth = 0
    end = len(lines)
    for i in range(start, len(lines) + 1):
        seg = lines[i - 1]
        if i == start:
            seg = seg[seg.index('"build"') :]
        depth += seg.count("{") - seg.count("}")
        if depth <= 0:
            end = i
            break
    needle = f'"{name}"'
    return next((i for i in range(start, end + 1) if needle in lines[i - 1]), None)


def _profile_facts(path: str, text: str, diags: list[Diagnostic]) -> list[EnvironmentFact]:
    try:
        doc: object = json.loads(text)
    except json.JSONDecodeError as exc:
        diags.append(_unparseable(path, "a profile manifest", exc))
        return []
    if not isinstance(doc, dict):
        return []
    build = cast("dict[str, object]", doc).get("build")
    if not isinstance(build, dict):
        return []
    lines = text.split("\n")
    out: list[EnvironmentFact] = []
    for key in sorted(cast("dict[str, object]", build)):
        line = _locate_profile_line(lines, key)
        if line is None:
            # The parse says the profile exists; a guessed line is not
            # evidence, so the fact is dropped and the absence is said.
            diags.append(_unparseable(path, f"a profile manifest's {key!r} key"))
            continue
        out.append(
            EnvironmentFact(
                name=canonical_env(key),
                source="profile",
                evidence=(Evidence(path, line, line),),
                attrs=(("profile", key),),
            )
        )
    return out


# --------------------------------------------------------------------------
# Source 3: CI workflow `environment:` keys
# --------------------------------------------------------------------------


def _branch_refs(on_node: yaml.Node | None) -> tuple[str, ...]:
    """Branch triggers of the workflow, as `ref` attribute values.

    The compose-style node walk reads the raw key text, so the YAML 1.1 trap
    (`on:` parsing as the boolean True under safe_load) never enters.
    """
    refs: set[str] = set()
    on = _mapping(on_node)
    for event in ("push", "pull_request"):
        branches = _mapping(on.get(event)).get("branches")
        if isinstance(branches, yaml.SequenceNode):
            for b in branches.value:
                if isinstance(b, yaml.ScalarNode) and isinstance(b.value, str):
                    refs.add(b.value)
    return tuple(sorted(refs))


def _environment_value(
    env_node: yaml.Node,
) -> str | None:
    """The environment name from either spelling: scalar, or `{name: ...}`."""
    if isinstance(env_node, yaml.ScalarNode):
        return _scalar(env_node)
    return _scalar(_mapping(env_node).get("name"))


def _workflow_facts(path: str, text: str, kind: str, diags: list[Diagnostic]) -> list[EnvironmentFact]:
    try:
        root = cast(
            "yaml.Node | None",
            yaml.compose(text, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
        )
    except (yaml.YAMLError, RecursionError) as exc:
        diags.append(_unparseable(path, "a CI workflow", exc))
        return []
    if root is None:
        return []
    if _depth(root) > 60:
        diags.append(_unparseable(path, "a CI workflow"))
        return []
    top = _mapping(root)
    refs = _branch_refs(top.get("on")) if kind == "ci-github" else ()

    if kind == "ci-github":
        jobs = _mapping(top.get("jobs"))
        holders = [(name, node) for name, node in sorted(jobs.items())]
    else:
        # GitLab names jobs at the top level, next to reserved keys like
        # `stages:`; rather than enumerate the reserved set, any mapping that
        # carries an `environment:` key is read as a job.
        holders = sorted(top.items())

    out: list[EnvironmentFact] = []
    for holder, body_node in holders:
        if not isinstance(body_node, yaml.MappingNode):
            continue
        env_node = _mapping(body_node).get("environment")
        if env_node is None:
            continue
        name = _environment_value(env_node)
        if not name or _DYNAMIC.search(name):
            # `environment: {name: review/$CI_COMMIT_REF_NAME}` names an
            # environment computed at run time; unknown stays unknown.
            continue
        line = _key_lines(body_node).get("environment", _line(env_node))
        attrs = [("job", holder)] + [("ref", r) for r in refs]
        out.append(
            EnvironmentFact(
                name=canonical_env(name),
                source="workflow",
                evidence=(Evidence(path, line, line),),
                attrs=tuple(sorted(attrs)),
            )
        )
    return out


# --------------------------------------------------------------------------
# Source 4: Dockerfile `ENV NODE_ENV=<value>`
# --------------------------------------------------------------------------


def _dockerfile_facts(path: str, text: str) -> list[EnvironmentFact]:
    out: list[EnvironmentFact] = []
    pending = ""
    start = 0
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not pending:
            start = i
        if line.startswith("#") and not pending:
            continue  # a comment is documentation, not a declaration
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        full = (pending + line).strip()
        pending = ""
        parts = full.split(None, 1)
        if not parts or parts[0].upper() != "ENV":
            continue
        rest = parts[1] if len(parts) > 1 else ""
        tokens = rest.split()
        assignments: list[list[str]] = []
        if tokens and "=" in tokens[0]:
            assignments = [t.split("=", 1) for t in tokens if "=" in t]
        elif len(tokens) >= 2:
            # Legacy form: `ENV NODE_ENV production`.
            assignments = [[tokens[0], tokens[1]]]
        for key, value in ((a[0], a[1]) for a in assignments):
            if key != "NODE_ENV" or not value or _DYNAMIC.search(value):
                continue
            out.append(
                EnvironmentFact(
                    name=canonical_env(value),
                    source="dockerfile",
                    evidence=(Evidence(path, start, i),),
                    attrs=(("variable", "NODE_ENV"),),
                )
            )
    return out


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def extract_environments(scan: Scan) -> EnvironmentFacts:
    """Every declared environment in the repository, each with its line.

    A repository with no signals yields no facts: environments are heavily
    implicit in real repos, and "no environments declared" is an honest
    absence, not a failure to be papered over.
    """
    facts: list[EnvironmentFact] = []
    diags: list[Diagnostic] = []

    # Workflows come from the scan: stage 1 already walked them, honouring
    # ignore files, and CI lives under `.github`, which the walk below prunes.
    for rec in scan.files:
        if rec.config_kind not in ("ci-github", "ci-gitlab"):
            continue
        # CI is where environments are declared, so the TOOLING role CI files
        # carry (`.github/` is tooling for architecture purposes) does not
        # exclude them here; test, generated and vendored roles still do.
        if rec.role in (FileRole.TEST, FileRole.GENERATED, FileRole.VENDORED):
            continue
        try:
            text = (scan.root / rec.path).read_text(encoding="utf8", errors="replace")
        except OSError as exc:
            diags.append(_unparseable(rec.path, "a CI workflow", exc))
            continue
        facts.extend(_workflow_facts(rec.path, text, rec.config_kind, diags))

    for rel, data in _walk_candidates(scan.root):
        if not _eligible_role(rel):
            continue
        name = rel.rsplit("/", 1)[-1]
        text = data.decode("utf8", errors="replace")
        if name == "eas.json":
            facts.extend(_profile_facts(rel, text, diags))
        elif _DOCKERFILE_RE.match(name) is not None:
            facts.extend(_dockerfile_facts(rel, text))
        if _ENV_RE.search(rel) is not None and _is_config_looking(rel):
            facts.extend(_filename_facts(rel, data))

    return EnvironmentFacts(
        facts=tuple(sorted(set(facts))),
        diagnostics=tuple(diags),
    )
