"""Mutation check for `svarupa setup` (P1-8).

Each entry deletes a property the component's commit message or docstrings
name, and a test must go red. Review #11's lesson measured at wave scale: a
mutation list that does not grow with the surface certifies nothing about the
new surface.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
SUITE = ["tests/test_setup.py", "tests/test_diagnostics.py", "tests/test_detect.py"]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "an existing differing file is overwritten instead of refused",
        "svarupa/setup/__init__.py",
        "    if clashes and not force:",
        "    if False:",
    ),
    (
        "--force is ignored (always refuse on clash)",
        "svarupa/setup/__init__.py",
        "    if clashes and not force:",
        "    if clashes:",
    ),
    (
        "only the first planned file is checked for collisions",
        "svarupa/setup/__init__.py",
        "    clashes = [p for p, content in planned if p.exists() and _differs(p, content)]",
        "    clashes = [p for p, content in planned[:1] if p.exists() and _differs(p, content)]",
    ),
    (
        "an unreadable existing file counts as identical",
        "svarupa/setup/__init__.py",
        "    except (OSError, UnicodeDecodeError):\n        return True",
        "    except (OSError, UnicodeDecodeError):\n        return False",
    ),
    (
        "the destination is never validated",
        "svarupa/setup/__init__.py",
        "    if not dest.is_dir():",
        "    if False:",
    ),
    (
        "an identical existing file is rewritten instead of reported unchanged",
        "svarupa/setup/__init__.py",
        "        if path.exists() and not _differs(path, content):",
        "        if False:",
    ),
    (
        "the setup command is unwired from the CLI",
        "svarupa/cli.py",
        '        if argv[:1] == ["setup"]:',
        "        if False:",
    ),
    (
        "the skill file loses its uppercase name",
        "svarupa/setup/skill.py",
        '        return ((".claude/skills/svarupa/SKILL.md", SKILL_MD),)',
        '        return ((".claude/skills/svarupa/skill.md", SKILL_MD),)',
    ),
    (
        "the skill document names a flag that does not exist",
        "svarupa/setup/skill.py",
        "use `--out DIR`. On very",
        "use `--output DIR`. On very",
    ),
    (
        "the workflow reads the committed base from a name that is not LOCK_NAME",
        "svarupa/setup/ci_github.py",
        ":.svarupa/architecture.lock",
        ":.svarupa/arch.lock",
    ),
    (
        "the workflow drifts the regenerated base to a name that is not LOCK_NAME",
        "svarupa/setup/ci_github.py",
        "--drift-base /tmp/base-artifact/architecture.lock",
        "--drift-base /tmp/base-artifact/arch.lock",
    ),
    (
        "the symlink check is deleted",
        "svarupa/setup/__init__.py",
        "    if escaping:",
        "    if False:",
    ),
    (
        "a broken symlink no longer counts as escaping",
        "svarupa/setup/__init__.py",
        "    if path.is_symlink():\n        return True",
        "    if path.is_symlink():\n        return False",
    ),
    (
        "a write failure escapes as a raw traceback again",
        "svarupa/setup/__init__.py",
        "        except OSError as exc:",
        "        except MemoryError as exc:",
    ),
    (
        "the CLI hardcodes force=True",
        "svarupa/cli.py",
        "    result = install(target, Path(args.dest), args.force)",
        "    result = install(target, Path(args.dest), True)",
    ),
    (
        "the workflow fetch is depth 1",
        "svarupa/setup/ci_github.py",
        "          fetch-depth: 0",
        "          fetch-depth: 1",
    ),
    (
        "the workflow install is unpinned",
        "svarupa/setup/ci_github.py",
        "        run: uv tool install 'svarupa==__SVARUPA_VERSION__'",
        "        run: uv tool install svarupa",
    ),
    (
        "the workflow swallows the exit code after writing the summary",
        "svarupa/setup/ci_github.py",
        '          exit "$code"',
        "          true",
    ),
    (
        "the workflow falls back silently when git cannot read the base",
        "svarupa/setup/ci_github.py",
        '          git cat-file -e "$BASE_SHA^{commit}"',
        "          true",
    ),
    (
        "the lockfile is foreign to claim() again",
        "svarupa/emit/__init__.py",
        "        owned = set(OWNED_FILES) | set(OWNED_DIRS) | set(TOLERATED_FILES)",
        "        owned = set(OWNED_FILES) | set(OWNED_DIRS)",
    ),
    (
        "the output directory is scanned as input again",
        "svarupa/detect.py",
        '        ".svarupa",',
        "",
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
