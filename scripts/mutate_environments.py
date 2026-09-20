"""Mutation check for the environment feature (extract, surface, lock).

Same discipline as the sibling harnesses (review #9's lesson): the list is
written against the *claims the feature makes* — its design doc, module
docstrings, and review #25's findings — not against interesting-looking
lines. Each entry disables exactly one claim; a test must go red:

* aliases fold to four canonical names and case is ignored;
* a path claims an env only when the token is delimited AND the file looks
  like configuration;
* `live`/`local` are strong in values, weak (stem-affix only) in paths;
* a dynamic value (`$VAR`, `${{ ... }}`) is unknown, never guessed;
* a profile citation is the key's line, never a value that quotes its name;
* test/vendored/generated files declare no environments;
* lockfile records carry the canonical name only — sources and paths are
  evidence, and evidence churns;
* a diagram frame links a service to an environment only through evidence.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = [
    "tests/test_environments.py",
    "tests/test_env_diagram.py",
    "tests/test_lock.py",
    "tests/test_diagnostics.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "prod folds to the wrong canonical environment",
        "svarupa/extract/environments.py",
        '    "prod": "production",',
        '    "prod": "staging",',
    ),
    (
        "any python file claims an env by name again",
        "svarupa/extract/environments.py",
        '    return ext == ".py" and any(d.lower() in _SETTINGS_DIRS for d in parts[:-1])',
        '    return ext == ".py"',
    ),
    (
        "an env token matches as a substring of a longer word",
        "svarupa/extract/environments.py",
        'rf"(?:^|[./_-])({_ENV_TOKEN})(?:[./_-]|$)"',
        'rf"(?:^|[./_-])({_ENV_TOKEN})"',
    ),
    (
        "a dynamic value is claimed as an environment name",
        "svarupa/extract/environments.py",
        '_DYNAMIC = re.compile(r"\\$|\\{\\{")',
        '_DYNAMIC = re.compile(r"(?!x)x")',
    ),
    (
        # The directory-segment form (`deploy/live/reload.yaml`) is enforced
        # at two independent sites (the walk gate and token extraction both
        # scope weak tokens to the stem), so no single-site mutation can
        # produce it. This mutant drops the end anchor instead: a weak token
        # as a non-final affix (`live-reload.yaml`) claims again.
        "a weak alias claims as a non-final affix again",
        "svarupa/extract/environments.py",
        '_WEAK_ENV_STEM_RE = re.compile(r"^(?:.+[._-])?(live|local)$", re.IGNORECASE)',
        '_WEAK_ENV_STEM_RE = re.compile(r"^(?:.+[._-])?(live|local)", re.IGNORECASE)',
    ),
    (
        "a profile citation lands on a value that quotes the name (F1)",
        "svarupa/extract/environments.py",
        '    key = re.compile(rf\'"{re.escape(name)}"\\s*:\')\n'
        "    return next((i for i in range(start, end + 1) if key.search(lines[i - 1])), None)",
        '    needle = f\'"{name}"\'\n'
        "    return next((i for i in range(start, end + 1) if needle in lines[i - 1]), None)",
    ),
    (
        "test and vendored files declare environments again",
        "svarupa/extract/environments.py",
        "        if not _eligible_role(rel):\n            continue",
        "        if False:\n            continue",
    ),
    (
        "environment lockfile records carry a churn field",
        "svarupa/lock/build.py",
        'sorted({Record("environment", (f.name,)) for f in graph.environments})',
        'sorted({Record("environment", (f.name, f.source)) for f in graph.environments})',
    ),
    (
        "environment records are dropped from the lockfile",
        "svarupa/lock/build.py",
        'sorted({Record("environment", (f.name,)) for f in graph.environments})',
        'sorted({Record("environment", (f.name,)) for f in ()})',
    ),
    (
        "a diagram frame links a service with no joining evidence",
        "svarupa/derive/system.py",
        '                    joins = (source == "filename" and nid.startswith(ev.file + "#service.")) or (\n'
        '                        source == "dockerfile" and node.attr("dockerfile") == ev.file\n'
        "                    )",
        "                    joins = True",
    ),
]


def main() -> int:
    failures: list[str] = []
    backup = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(ROOT / "svarupa", backup)
    try:
        for name, rel, old, new in MUTATIONS:
            path = ROOT / rel
            text = path.read_text(encoding="utf8")
            if old not in text:
                print(f"SKIP (pattern not found)  {name}")
                failures.append(f"{name}: pattern not found")
                continue
            path.write_text(text.replace(old, new, 1), encoding="utf8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", *SUITE, "-q", "-x"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(ROOT / "svarupa")
            shutil.copytree(backup, ROOT / "svarupa")
            if proc.returncode == 0:
                print(f"SURVIVED  {name}")
                failures.append(f"{name}: no test failed")
            else:
                caught = [
                    ln.split("::")[-1]
                    for ln in proc.stdout.splitlines()
                    if ln.startswith("FAILED")
                ]
                print(f"caught    {name}  ->  {caught[0] if caught else 'error'}")
    finally:
        if not (ROOT / "svarupa").exists():  # pragma: no cover - safety net
            shutil.copytree(backup, ROOT / "svarupa")
    if failures:
        print("\nnot caught:")
        for f in failures:
            print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
