"""Environment extraction, and its honesty rules.

The contract under test:

* every environment fact carries file:line evidence — a filename claim cites
  the file itself (line 1, or `(0, 0)` for an empty file), a manifest or
  workflow claim cites the declaring key's own line, never a guessed one;
* alias folding is case-insensitive onto four canonical names, and an
  unrecognized token is kept verbatim — nothing is lost, nothing invented;
* decoys claim nothing: `development-notes.md` is prose, `prod_utils.py` is a
  helper, `ARG NODE_ENV=...` is a build default, a `# ENV NODE_ENV=prod`
  comment is documentation, a `$VAR` value is dynamic, and a test fixture's
  `values-prod.yaml` declares nothing about the deployed system;
* a repository with no signals reports no environments — an honest absence,
  not a failure — and contradictory declarations coexist (a Dockerfile saying
  `development` next to a `values-prod.yaml` is reported, never resolved by
  fiat);
* output is deterministic: facts are sorted, order-independent, and free of
  timestamps and dict-order dependence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import EnvironmentFact
from svarupa.extract.environments import canonical_env, extract_environments


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def facts(root: Path) -> tuple[EnvironmentFact, ...]:
    return extract_environments(detect(root)).facts


def by_name(root: Path) -> dict[str, EnvironmentFact]:
    return {f.name: f for f in facts(root)}


# --------------------------------------------------------------------------
# Source 1: env-suffixed config filenames
# --------------------------------------------------------------------------


def test_env_suffixed_config_filenames_fold_to_canonical_names(tmp_path: Path) -> None:
    write(tmp_path, "deploy/values-dev.yaml", "replicas: 1\n")
    write(tmp_path, "deploy/values-prod.yaml", "replicas: 3\n")
    write(tmp_path, "deploy/values-uat.yaml", "replicas: 2\n")
    got = by_name(tmp_path)
    assert set(got) == {"development", "staging", "production"}
    assert all(f.source == "filename" for f in got.values())
    prod = got["production"]
    assert prod.attrs == (("path", "deploy/values-prod.yaml"),)
    assert prod.evidence[0].file == "deploy/values-prod.yaml"
    assert (prod.evidence[0].start_line, prod.evidence[0].end_line) == (1, 1)


def test_a_settings_module_named_for_an_env_is_a_claim(tmp_path: Path) -> None:
    write(tmp_path, "settings/production.py", "DEBUG = False\n")
    write(tmp_path, "settings/local.py", "DEBUG = True\n")
    got = by_name(tmp_path)
    assert set(got) == {"production", "development"}
    assert got["production"].evidence[0].file == "settings/production.py"


def test_an_empty_config_file_is_cited_as_itself(tmp_path: Path) -> None:
    """`(0, 0)` is the only legal whole-file citation, and it is legal here."""
    write(tmp_path, "config/prod.yaml", "")
    ev = by_name(tmp_path)["production"].evidence[0]
    assert (ev.start_line, ev.end_line) == (0, 0)


def test_filename_decoys_claim_nothing(tmp_path: Path) -> None:
    """Prose and helpers fail one of the two gates: the token must be
    delimited AND the file must look like configuration."""
    write(tmp_path, "docs/development-notes.md", "# notes\n")
    write(tmp_path, "src/prod_utils.py", "x = 1\n")
    write(tmp_path, "src/devtools.py", "x = 1\n")  # no delimiter: not a token
    assert facts(tmp_path) == ()


def test_a_config_named_for_a_test_env_is_not_folded(tmp_path: Path) -> None:
    """`test` is a file role, not a deploy env: it is neither folded nor
    claimed as an environment name."""
    write(tmp_path, "deploy/values-test.yaml", "x: 1\n")
    assert facts(tmp_path) == ()


def test_an_unrecognized_token_is_kept_verbatim(tmp_path: Path) -> None:
    write(tmp_path, "deploy/values-bluegreen.yaml", "x: 1\n")
    # `bluegreen` is not an env token at all, so no fact; `qa` verbatim.
    write(tmp_path, "deploy/values-qa.yaml", "x: 1\n")
    got = by_name(tmp_path)
    assert set(got) == {"qa"}


def test_test_and_vendored_configs_declare_no_environments(tmp_path: Path) -> None:
    """The same gate semantics applies to manifests: a fixture or a vendored
    tree says nothing about the production architecture."""
    write(tmp_path, "tests/fixtures/values-prod.yaml", "x: 1\n")
    write(tmp_path, "vendor/lib/values-prod.yaml", "x: 1\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    assert facts(tmp_path) == ()


def test_a_profile_value_mentioning_a_later_profile_is_not_cited(tmp_path: Path) -> None:
    """Review #25 F1: `"channel": "production"` inside the `preview` profile
    contains the quoted name; citing that value line for the `production`
    profile points the evidence at a line that does not make the claim."""
    write(
        tmp_path,
        "eas.json",
        "{\n"
        '  "build": {\n'
        '    "preview": { "channel": "production" },\n'  # line 3: the decoy
        '    "production": {}\n'  # line 4: the real key
        "  }\n"
        "}\n",
    )
    got = {f.name: f for f in facts(tmp_path)}
    assert got["production"].evidence[0].start_line == 4
    assert got["staging"].evidence[0].start_line == 3  # preview folds, its own key line


def test_live_and_local_directory_segments_claim_nothing(tmp_path: Path) -> None:
    """Review #25 F2: as bare path segments the weak aliases are ordinary
    words — `deploy/live/` is live-reload, `config/local/` is local
    overrides; neither is a deploy environment."""
    write(tmp_path, "deploy/live/reload.yaml", "x: 1\n")
    write(tmp_path, "config/local/overrides.yaml", "x: 1\n")
    write(tmp_path, "deploy/liveness.yaml", "x: 1\n")
    assert facts(tmp_path) == ()


def test_weak_aliases_claim_as_stem_affixes_and_as_values(tmp_path: Path) -> None:
    """The same tokens are strong where they were written to NAME an
    environment: the stem's final affix (`values-live.yaml`) and a workflow's
    `environment:` value."""
    write(tmp_path, "deploy/values-live.yaml", "x: 1\n")
    write(tmp_path, "settings/local.py", "DEBUG = True\n")
    write(
        tmp_path,
        ".github/workflows/deploy.yml",
        "on: push\njobs:\n  ship:\n    environment: live\n",
    )
    got = facts(tmp_path)
    assert {(f.name, f.source) for f in got} == {
        ("production", "filename"),
        ("development", "filename"),
        ("production", "workflow"),
    }


def test_a_value_like_stem_does_not_overreach(tmp_path: Path) -> None:
    """`live-reload.yaml` has the token as a prefix, not the final affix."""
    write(tmp_path, "deploy/live-reload.yaml", "x: 1\n")
    assert facts(tmp_path) == ()


# --------------------------------------------------------------------------
# Source 2: profile manifests (eas.json)
# --------------------------------------------------------------------------

EAS = """\
{
  "cli": { "version": ">= 5.0.0" },
  "build": {
    "development": { "developmentClient": true },
    "preview": { "distribution": "internal" },
    "production": {}
  }
}
"""


def test_eas_build_profiles_are_environments_citing_the_key_line(tmp_path: Path) -> None:
    write(tmp_path, "eas.json", EAS)
    got = {f.attrs[0][1]: f for f in facts(tmp_path) if f.source == "profile"}
    # preview folds to staging; the verbatim key survives in the attrs.
    assert {f.name for f in got.values()} == {"development", "staging", "production"}
    lines = {k: f.evidence[0].start_line for k, f in got.items()}
    assert lines == {"development": 4, "preview": 5, "production": 6}, lines


def test_a_malformed_profile_manifest_degrades_to_a_diagnostic(tmp_path: Path) -> None:
    write(tmp_path, "eas.json", "{ not json\n")
    got = extract_environments(detect(tmp_path))
    assert got.facts == ()
    assert any(d.code == "SVA-X-010" for d in got.diagnostics)


def test_a_profile_key_whose_line_cannot_be_located_is_dropped_not_guessed(
    tmp_path: Path,
) -> None:
    """JSON parses duplicate keys (last wins); the scanner finds the first.
    `"build"` as a plain string value means there is no object to scope the
    search to, so the honest answer is no fact and a diagnostic."""
    write(
        tmp_path,
        "eas.json",
        '{\n  "build": "not-an-object",\n  "x": {"development": {}}\n}\n',
    )
    assert facts(tmp_path) == ()


# --------------------------------------------------------------------------
# Source 3: CI workflows
# --------------------------------------------------------------------------

WORKFLOW = """\
name: deploy
on:
  push:
    branches: [main]
jobs:
  ship:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ./deploy.sh
  stage:
    runs-on: ubuntu-latest
    environment:
      name: qa
      url: https://qa.example.com
"""


def test_github_environment_keys_cite_the_key_and_carry_the_ref(tmp_path: Path) -> None:
    write(tmp_path, ".github/workflows/deploy.yml", WORKFLOW)
    got = {f.name: f for f in facts(tmp_path)}
    assert set(got) == {"production", "qa"}
    prod = got["production"]
    assert prod.source == "workflow"
    assert prod.evidence[0].file == ".github/workflows/deploy.yml"
    assert prod.evidence[0].start_line == 8, "the `environment:` key's own line"
    # Branch triggers are attributes on the environment, not environments.
    assert ("ref", "main") in prod.attrs
    assert ("job", "ship") in prod.attrs
    assert "main" not in got
    qa = got["qa"]
    assert qa.evidence[0].start_line == 13, "the mapping form still cites the key"


def test_gitlab_environment_keys_are_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".gitlab-ci.yml",
        "stages: [deploy]\n"
        "deploy_prod:\n"
        "  stage: deploy\n"
        "  environment: prod\n"
        "  script: ./ship.sh\n",
    )
    got = by_name(tmp_path)
    assert set(got) == {"production"}
    assert got["production"].evidence[0].start_line == 4


def test_a_dynamic_workflow_environment_is_unknown_not_guessed(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".gitlab-ci.yml",
        "review:\n  environment:\n    name: review/$CI_COMMIT_REF_NAME\n",
    )
    assert facts(tmp_path) == ()


def test_a_workflow_that_is_not_yaml_degrades_to_a_diagnostic(tmp_path: Path) -> None:
    write(tmp_path, ".github/workflows/broken.yml", "jobs: [unclosed\n")
    got = extract_environments(detect(tmp_path))
    assert got.facts == ()
    assert any(d.code == "SVA-X-010" for d in got.diagnostics)


# --------------------------------------------------------------------------
# Source 4: Dockerfile ENV NODE_ENV
# --------------------------------------------------------------------------


def test_dockerfile_node_env_cites_the_env_line(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Dockerfile",
        "FROM node:20\n"  # line 1
        "WORKDIR /app\n"  # line 2
        "ENV NODE_ENV=production\n",  # line 3  <- the citation
    )
    got = by_name(tmp_path)
    assert set(got) == {"production"}
    f = got["production"]
    assert f.source == "dockerfile"
    assert f.evidence[0].file == "Dockerfile"
    assert f.evidence[0].start_line == 3
    assert ("variable", "NODE_ENV") in f.attrs


def test_arg_defaults_comments_and_dynamic_values_claim_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Dockerfile",
        "FROM node:20\n"
        "# ENV NODE_ENV=prod is what we used to do\n"
        "ARG NODE_ENV=development\n"
        "ENV NODE_ENV=$BUILD_ENV\n",
    )
    assert facts(tmp_path) == ()


def test_dockerfile_value_case_folds_and_legacy_syntax_is_read(tmp_path: Path) -> None:
    write(tmp_path, "Dockerfile.web", "FROM node:20\nENV NODE_ENV Prod\n")
    got = by_name(tmp_path)
    assert set(got) == {"production"}


def test_a_node_env_in_a_vendored_dockerfile_claims_nothing(tmp_path: Path) -> None:
    write(tmp_path, "vendor/x/Dockerfile", "FROM node:20\nENV NODE_ENV=production\n")
    assert facts(tmp_path) == ()


# --------------------------------------------------------------------------
# The honesty rules
# --------------------------------------------------------------------------


def test_a_repo_with_no_signals_reports_no_environments(tmp_path: Path) -> None:
    """Environments are heavily implicit in real repos; absence is reported
    honestly rather than fabricated into a claim."""
    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    got = extract_environments(detect(tmp_path))
    assert got.facts == ()


def test_contradictory_declarations_coexist(tmp_path: Path) -> None:
    """`NODE_ENV=development` next to a prod values file is a contradiction;
    it is reported, never resolved by fiat."""
    write(tmp_path, "Dockerfile", "FROM node:20\nENV NODE_ENV=development\n")
    write(tmp_path, "deploy/values-prod.yaml", "x: 1\n")
    got = facts(tmp_path)
    assert {(f.name, f.source) for f in got} == {
        ("development", "dockerfile"),
        ("production", "filename"),
    }


def test_alias_folding_is_case_insensitive() -> None:
    assert canonical_env("DEV") == "development"
    assert canonical_env("Uat") == "staging"
    assert canonical_env("LIVE") == "production"
    assert canonical_env("qa") == "qa"
    assert canonical_env("bluegreen") == "bluegreen"


# --------------------------------------------------------------------------
# Determinism and wiring
# --------------------------------------------------------------------------


def test_facts_are_sorted_and_run_stable(tmp_path: Path) -> None:
    write(tmp_path, "deploy/values-prod.yaml", "x: 1\n")
    write(tmp_path, "deploy/values-dev.yaml", "x: 1\n")
    write(tmp_path, "Dockerfile", "FROM node:20\nENV NODE_ENV=production\n")
    write(tmp_path, ".github/workflows/deploy.yml", WORKFLOW)
    write(tmp_path, "eas.json", EAS)
    first = facts(tmp_path)
    assert first == tuple(sorted(first))
    assert first == facts(tmp_path), "repeated runs produce identical facts"


def test_facts_land_on_the_extract_result(tmp_path: Path) -> None:
    """Stage-2 wiring: what the extractor finds is what `extract` returns."""
    write(tmp_path, "deploy/values-prod.yaml", "x: 1\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    scan = detect(tmp_path)
    result = extract(scan, declared_dependencies(scan))
    assert {(f.name, f.source) for f in result.environments} == {
        ("production", "filename")
    }


# --------------------------------------------------------------------------
# Surfacing: graph.json, REPORT.md, the CLI, and the lockfile boundary
# --------------------------------------------------------------------------


def graph_of(root: Path):
    from svarupa.build import build

    scan = detect(root)
    return build(scan, extract(scan, declared_dependencies(scan)), strict=False)


def _env_repo(root: Path) -> None:
    write(root, "src/app.py", "x = 1\n")
    write(root, "deploy/values-prod.yaml", "replicas: 3\n")
    write(root, "Dockerfile", "FROM node:20\nENV NODE_ENV=staging\n")
    write(root, ".github/workflows/deploy.yml", WORKFLOW)


def test_environments_flow_from_extract_result_into_the_graph(tmp_path: Path) -> None:
    _env_repo(tmp_path)
    g = graph_of(tmp_path)
    assert sorted({e.name for e in g.environments}) == ["production", "qa", "staging"]
    # The graph never grows environment NODES: derivers walk graph.nodes, and
    # an unknown kind there would reach diagrams that do not expect it.
    assert not any(n.kind.value == "environment" for n in g.nodes.values())


def test_environment_nodes_in_graph_json_carry_their_evidence(tmp_path: Path) -> None:
    from svarupa.emit.data import graph_json

    _env_repo(tmp_path)
    doc = graph_json(graph_of(tmp_path))
    nodes = {n["id"]: n for n in doc["nodes"] if n["kind"] == "environment"}
    assert set(nodes) == {"env:production", "env:qa", "env:staging"}
    prod = nodes["env:production"]
    files = {e["file"] for e in prod["evidence"]}
    assert {"deploy/values-prod.yaml", ".github/workflows/deploy.yml"} <= files
    assert prod["attrs"]["sources"] == "filename, workflow"
    assert prod["attrs"]["refs"] == "main"
    # One edge per declaring file, and every citation is a real location.
    edges = [e for e in doc["edges"] if e["dst"] == "env:production"]
    assert {(e["src"], e["kind"], e["context"]) for e in edges} == {
        ("deploy/values-prod.yaml", "deploys", "deploy"),
        (".github/workflows/deploy.yml", "deploys", "deploy"),
    }
    for e in edges:
        assert all(x["start_line"] >= 1 for x in e["evidence"])
    # A staging claim from the Dockerfile alone cites the ENV line.
    stage = nodes["env:staging"]
    assert [(e["file"], e["start_line"]) for e in stage["evidence"]] == [("Dockerfile", 2)]
    assert stage["attrs"]["sources"] == "dockerfile"


def test_graph_json_is_byte_stable_with_environments(tmp_path: Path) -> None:
    import json

    from svarupa.emit.data import graph_json

    _env_repo(tmp_path)
    g = graph_of(tmp_path)

    def render() -> str:
        return json.dumps(graph_json(g), indent=2, sort_keys=True)

    assert render() == render()


def test_the_report_lists_environments_with_citations(tmp_path: Path) -> None:
    from svarupa.emit.report import render_report

    _env_repo(tmp_path)
    text = render_report("repo", graph_of(tmp_path), {}, {}, (), ())
    assert "## Environments" in text
    assert "**production**" in text
    assert "`deploy/values-prod.yaml:1`" in text
    assert "`Dockerfile:2`" in text
    assert "No environment declarations" not in text


def test_the_report_states_an_honest_absence(tmp_path: Path) -> None:
    from svarupa.emit.report import render_report

    write(tmp_path, "src/app.py", "x = 1\n")
    text = render_report("repo", graph_of(tmp_path), {}, {}, (), ())
    assert "## Environments" in text
    assert "No environment declarations were found" in text


def test_the_cli_summary_names_environments_or_their_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from svarupa.cli import main

    with_env = tmp_path / "with"
    _env_repo(with_env)
    assert main([str(with_env), "--out", str(tmp_path / "out1")]) == 0
    out = capsys.readouterr().out
    assert "  environments: production, qa, staging (4 declaration(s))\n" in out

    without = tmp_path / "without"
    write(without, "src/app.py", "x = 1\n")
    assert main([str(without), "--out", str(tmp_path / "out2")]) == 0
    out = capsys.readouterr().out
    assert "  environments: none declared\n" in out


def test_environment_facts_add_only_environment_records(tmp_path: Path) -> None:
    """The lockfile is a committed file: environment declarations add their
    own record lines and churn nothing else — no paths, no sources, no refs,
    because a workflow rename is not an architecture change."""
    from svarupa import __version__
    from svarupa.lock import build_lock

    write(tmp_path, "src/app.py", "x = 1\n")
    before = build_lock(graph_of(tmp_path), __version__).lockfile.render()
    assert "environment" not in before, "no declarations, no records, honest absence"
    _env_repo(tmp_path)
    after = build_lock(graph_of(tmp_path), __version__).lockfile.render()
    added = set(after.splitlines()) - set(before.splitlines())
    assert added == {
        "environment\tproduction",
        "environment\tqa",
        "environment\tstaging",
    }, added
    assert set(before.splitlines()) <= set(after.splitlines()), "nothing removed"
    # The records are names only: the evidence stays in graph.json.
    assert all("\t" not in line[len("environment\t") :] for line in added)


def test_environment_records_are_sorted_and_byte_stable(tmp_path: Path) -> None:
    from svarupa import __version__
    from svarupa.lock import build_lock

    _env_repo(tmp_path)
    first = build_lock(graph_of(tmp_path), __version__).lockfile.render()
    second = build_lock(graph_of(tmp_path), __version__).lockfile.render()
    assert first == second
    env_lines = [ln for ln in first.splitlines() if ln.startswith("environment\t")]
    assert env_lines == sorted(env_lines)


def test_a_gained_environment_shows_as_one_green_line(tmp_path: Path) -> None:
    """The pull-request reading the kind exists for."""
    from svarupa import __version__
    from svarupa.lock import build_lock, diff

    write(tmp_path, "src/app.py", "x = 1\n")
    base = build_lock(graph_of(tmp_path), __version__).lockfile
    write(tmp_path, "deploy/values-stage.yaml", "x: 1\n")
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    rendered = diff(base, head).render()
    assert "+ environment\tstaging" in rendered
    assert "- environment" not in rendered


def test_a_schema_16_base_diffs_the_new_records_as_opaque_adds(tmp_path: Path) -> None:
    """The additive path: a 1.6-stamped base predates `environment`, so the
    diff attributes the new lines to the tool upgrade (SVA-L-013), never to
    the PR."""
    from svarupa import __version__
    from svarupa.lock import Lockfile, build_lock, diff

    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "deploy/values-prod.yaml", "x: 1\n")
    head = build_lock(graph_of(tmp_path), __version__).lockfile
    old_base = Lockfile.parse(
        "# svarupa 0.1.0\n# schema 1.6\n# grammars python@0.25.0\nmodule\tsrc\n"
    )
    delta = diff(old_base, head)
    assert "+ environment\tproduction" in delta.render()
    assert "SVA-L-013" in [d.code for d in delta.diagnostics]


def test_build_drops_an_environment_fact_with_invented_evidence(
    tmp_path: Path,
) -> None:
    """The independent re-check applies to environment facts too, including
    ones citing files the scan never recorded: the line count is read from
    disk, and a citation past the end of the file is a dropped fact."""
    from dataclasses import replace as dc_replace

    from svarupa.model import Evidence

    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "deploy/values-prod.yaml", "replicas: 3\n")
    scan = detect(tmp_path)
    extracted = extract(scan, declared_dependencies(scan))
    bogus = EnvironmentFact(
        name="production",
        source="filename",
        evidence=(Evidence("deploy/values-prod.yaml", 99, 99),),
    )
    from svarupa.build import build

    g = build(scan, dc_replace(extracted, environments=(bogus,)), strict=False)
    assert g.environments == ()
    assert any(d.code == "SVA-B-002" for d in g.diagnostics)


def test_an_empty_config_file_survives_the_build_recheck(tmp_path: Path) -> None:
    """An empty `config/prod.yaml` is cited as itself, `(0, 0)`: the one
    whole-file citation the evidence contract allows."""
    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "config/prod.yaml", "")
    g = graph_of(tmp_path)
    assert [f.name for f in g.environments] == ["production"]
    assert (g.environments[0].evidence[0].start_line) == 0
