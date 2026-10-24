"""The rules text every AI coding tool gets."""

from __future__ import annotations

from pathlib import Path

from svarupa.health import CATALOG
from svarupa.setup.rules import repo_rules, rules_body, workflow_text
from svarupa.setup.skill import SKILL_MD
from tests.test_design_outputs import layered


def test_the_workflow_is_the_skill_section() -> None:
    text = workflow_text()
    assert text.startswith("## Before and after you write code\n")
    assert text in SKILL_MD
    assert "svarupa check" in text and "\n## " not in text.rstrip()[3:]


def test_repo_rules_list_forbidden_directions_and_every_check(tmp_path: Path) -> None:
    layered(tmp_path)
    text = repo_rules(tmp_path)
    assert text.startswith("## This repository's rules\n")
    assert "layered" in text
    assert "must not import" in text and "api" in text
    for c in CATALOG:
        assert f"- {c.id} (" in text
    assert "svarupa setup" in text and "--update" in text


def test_rules_for_an_empty_repository_say_how_to_refresh(tmp_path: Path) -> None:
    text = repo_rules(tmp_path)
    assert "no rules could be read yet" in text
    assert "--update" in text
    assert "- query-in-loop (" in text


def test_the_body_starts_with_a_title_and_holds_both_parts(tmp_path: Path) -> None:
    layered(tmp_path)
    body = rules_body(tmp_path)
    assert body.startswith("# Svarupa\n")
    assert "## Before and after you write code" in body
    assert "## This repository's rules" in body
