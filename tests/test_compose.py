"""docker-compose extraction, and its hostile-input posture.

A compose file is committed, merged by humans and hand-edited, so malformed
input is the expected case, and review #11 demonstrated that the first version
covered the bytes and not the parser: PyYAML's scanner is recursive, and one
file of nested flow brackets raised RecursionError past every guard and took
the entire run down. A parser's exception list is what it raises in practice,
not what its docstring names.
"""

from __future__ import annotations

from pathlib import Path

from svarupa import __version__
from svarupa.build import build
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.compose import extract_compose
from svarupa.lock import build_lock
from svarupa.model import NodeKind


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def facts(root: Path):
    return extract_compose(detect(root))


# --------------------------------------------------------------------------
# The happy path, asserted precisely
# --------------------------------------------------------------------------


def test_the_citation_is_the_service_keys_own_line(tmp_path: Path) -> None:
    """Line-accurate evidence was the wave's headline claim, and it was off by
    one: the body node's mark is its first child, so `minio:` declared at line
    90 cited line 91. The citation is where a human sees the name."""
    write(
        tmp_path,
        "docker-compose.yml",
        "# a comment\n"  # line 1
        "services:\n"  # line 2
        "  web:\n"  # line 3  <- the citation
        "    image: nginx\n"  # line 4
        "  db:\n"  # line 5  <- the citation
        "    image: postgres:16\n",
    )
    got = {n.label: n.evidence[0].start_line for n in facts(tmp_path).nodes}
    assert got == {"web": 3, "db": 5}, got


def test_a_registry_port_does_not_break_image_classification(tmp_path: Path) -> None:
    """`registry.example.com:5000/postgres:16` split on `:` before `/`, so the
    registry port truncated the name and a postgres classified as a plain
    service."""
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n"
        "  db:\n"
        "    image: registry.example.com:5000/postgres:16\n"
        "  mq:\n"
        "    image: internal:443/team/rabbitmq:3\n",
    )
    kinds = {n.label: n.kind for n in facts(tmp_path).nodes}
    assert kinds["db"] is NodeKind.DATASTORE
    assert kinds["mq"] is NodeKind.QUEUE


def test_an_override_file_is_part_of_the_system(tmp_path: Path) -> None:
    """docker-compose auto-loads the override file; a service defined only
    there was silently missing from the view whose claim is the deployed
    shape."""
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    image: nginx\n")
    write(
        tmp_path,
        "docker-compose.override.yml",
        "services:\n  debug:\n    image: nginx\n",
    )
    write(tmp_path, "docker-compose.prod.yml", "services:\n  cdn:\n    image: nginx\n")
    labels = {n.label for n in facts(tmp_path).nodes}
    assert {"web", "debug", "cdn"} <= labels


def test_a_merge_key_is_diagnosed_as_under_described(tmp_path: Path) -> None:
    """`<<: *base` is not expanded, so an inherited image or depends_on is
    invisible. Saying so beats silently drawing a plain service."""
    write(
        tmp_path,
        "docker-compose.yml",
        "x-base: &base\n"
        "  image: postgres:16\n"
        "services:\n"
        "  db:\n"
        "    <<: *base\n"
        "    ports: ['5432:5432']\n",
    )
    got = facts(tmp_path)
    assert any(d.code == "SVA-X-005" and "merge key" in d.message for d in got.diagnostics), [
        d.message for d in got.diagnostics
    ]


# --------------------------------------------------------------------------
# Hostile input: one file's failure costs that file
# --------------------------------------------------------------------------


def bomb(root: Path) -> None:
    write(root, "docker-compose.yml", "x: " + "[" * 20000 + "]" * 20000 + "\n")


def test_a_recursion_bomb_degrades_to_a_diagnostic(tmp_path: Path) -> None:
    """This exact file killed the entire run with a raw traceback: the depth
    guard ran after parsing, and PyYAML's recursive scanner never got there."""
    bomb(tmp_path)
    write(tmp_path, "src/__init__.py", "")
    write(tmp_path, "src/app.py", "x = 1\n")

    got = facts(tmp_path)  # must not raise
    assert any(d.code == "SVA-X-005" for d in got.diagnostics)
    assert got.nodes == ()

    # And the rest of the pipeline still runs over the rest of the repository.
    scan = detect(tmp_path)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    assert "src" in graph.modules
    lock = build_lock(graph, __version__)
    assert any(r.kind == "module" for r in lock.lockfile.records)


def test_an_oversized_compose_file_is_skipped_with_a_count(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  web:\n    image: nginx\n" + "# pad\n" * 300_000,
    )
    got = facts(tmp_path)
    assert got.nodes == ()
    assert any("bytes" in d.message and d.code == "SVA-X-005" for d in got.diagnostics)


def test_services_as_a_list_is_a_malformed_file_not_an_empty_system(
    tmp_path: Path,
) -> None:
    """Zero nodes with zero diagnostics made the system view say 'no deployment
    configuration was found' about a repository that has one: a wrong sentence
    rather than a missing feature."""
    write(tmp_path, "docker-compose.yml", "services:\n  - web\n  - db\n")
    got = facts(tmp_path)
    assert got.nodes == ()
    assert any(d.code == "SVA-X-005" and "services" in d.message for d in got.diagnostics)


def test_a_hostile_service_name_stays_data(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        'services:\n  "<img src=x onerror=alert(1)>":\n    image: nginx\n',
    )
    got = facts(tmp_path)
    assert [n.label for n in got.nodes] == ["<img src=x onerror=alert(1)>"]
    # The emitters escape it; extraction records the fact as it is.


def test_a_multi_document_file_reads_its_first_document(tmp_path: Path) -> None:
    """`yaml.compose` reads one document; a second one after `---` raises
    ComposerError inside PyYAML, and that must degrade like any other parse
    failure."""
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  web:\n    image: nginx\n---\nservices:\n  other:\n    image: nginx\n",
    )
    got = facts(tmp_path)  # must not raise
    labels = {n.label for n in got.nodes}
    assert labels in ({"web"}, set()), labels
    if not labels:
        assert any(d.code == "SVA-X-005" for d in got.diagnostics)
