"""Committed secrets: found in any file, and the value never leaves it.

Fake keys are assembled at runtime so this file never holds a string that a
secret scanner (or GitHub push protection) would flag.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svarupa.cli import main
from svarupa.health.secrets import secret_checks

AWS = "AKIA" + "Q7XZ" + "M2PL" + "W9RT" + "K4VB"
GH = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6"
PASSWORD = "Zq8" + "rT2vLp9w"


def hits(texts: dict[str, str]) -> list[tuple[str, int, str]]:
    return [
        (v.evidence[0].file, v.evidence[0].start_line, v.message) for v in secret_checks(texts)
    ]


def test_key_formats_and_assignments_are_found_by_line() -> None:
    texts = {
        "app/settings.py": f"A = 1\nAWS_KEY = '{AWS}'\n",
        "deploy/.env": f"GITHUB_TOKEN={GH}\nDB_PASSWORD={PASSWORD}\n",
        "app/conf.yaml": f'db:\n  password: "{PASSWORD}"\n',
    }
    found = hits(texts)
    assert [(f, line) for f, line, _ in found] == [
        ("app/conf.yaml", 2),
        ("app/settings.py", 2),
        ("deploy/.env", 1),
        ("deploy/.env", 2),
    ]
    assert found[1][2].startswith("AWS access key")


def test_placeholders_and_env_reads_are_skipped() -> None:
    texts = {
        "a.py": (
            "password = 'changeme123'\n"
            "API_KEY = os.environ['API_KEY']\n"
            "token = '${TOKEN_VALUE}'\n"
            "secret = '<your-secret-here>'\n"
            "aws = 'AKIAIOSFODNN7EXAMPLE'\n"
            "password = ''\n"
        ),
        ".env.example": "DB_PASSWORD=your_password_here\n",
    }
    assert hits(texts) == []


def test_message_has_a_fingerprint_and_never_the_value() -> None:
    (v,) = secret_checks({"a.py": f"key = '{AWS}'\n"})
    assert AWS not in v.message and AWS[:8] not in v.message
    assert "fingerprint" in v.message


def test_secret_value_reaches_no_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "m.py").write_text(f"KEY = '{AWS}'\n", encoding="utf8")
    (tmp_path / ".env").write_text(f"DB_PASSWORD={PASSWORD}\n", encoding="utf8")
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = [v["check"] for v in gj["health"]["violations"]]
    assert checks.count("hardcoded-secret") == 2
    assert gj["health"]["ratings"]["security"] == "E"
    assert main(["query", str(tmp_path / ".svarupa"), "get_health", "--json"]) == 0
    out += capsys.readouterr().out
    for path in (tmp_path / ".svarupa").rglob("*"):
        if path.is_file():
            data = path.read_text(encoding="utf8", errors="replace")
            assert AWS not in data and PASSWORD not in data, path.name
    assert AWS not in out and PASSWORD not in out


def test_a_pattern_or_sentence_is_not_a_secret() -> None:
    texts = {
        "m.py": (
            '_ENV_TOKEN = "dev|development|qa|staging|stage|uat|preprod|prod"\n'
            'PASSWORD_HELP = "Use at least 12 characters"\n'
            'TOKEN_RE = "(?:[A-Za-z]+)"\n'
        )
    }
    assert hits(texts) == []


def test_a_django_style_key_with_symbols_is_found() -> None:
    key = "c6u0-9c!7nilj_ysat" + "sda0(f@e_2mws2f!6m0n^o*4#*q#kzp)"
    assert [line for _, line, _ in hits({"settings.py": f'SECRET_KEY = "{key}"\n'})] == [1]


def test_values_that_name_themselves_or_are_hashes_are_not_secrets() -> None:
    texts = {
        ".env.example": (
            "SMTP_PASSWORD=email-server-password\n"
            "JWT_ACCESS_SECRET=nestjsPrismaAccessSecret\n"
            "SECRET_KEY=changethis\n"
        ),
        "tokens.js": "  RESET_PASSWORD: 'resetPassword',\n",
        "t.py": 'data = {"password": "password123"}\n',
        "fixture.json": '  "password": "pbkdf2_sha256$600000$yzcRrbI8n9abcDEF$Q1w2E3r4"\n',
        "docs.yml": "  token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijKLMNOP\n",
    }
    assert hits(texts) == []


def test_digit_only_values_are_not_secrets() -> None:
    assert hits({"t.go": '`{"username": "u", "password": "0123456789"}`\n'}) == []


def test_fingerprint_is_not_a_plain_hash_of_the_value() -> None:
    import hashlib

    (v,) = secret_checks({"a.py": f"key = '{AWS}'\n"})
    assert hashlib.sha256(AWS.encode()).hexdigest()[:12] not in v.message
    assert secret_checks({"a.py": f"key = '{AWS}'\n"})[0].message == v.message
