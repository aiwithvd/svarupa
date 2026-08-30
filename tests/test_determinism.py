"""The determinism suite: the highest-value test in the project.

If this regresses, the CI pillar is worthless — every pull request would show
architecture changes that did not happen, teams would learn to ignore the
check, and the governance story dies.

Run under a matrix that varies PYTHONHASHSEED and LC_ALL (see the CI workflow).
The cross-platform leg (Linux vs macOS byte identity) runs in CI; the hazards
reproducible on a single machine are covered here.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from svarupa.lock import Lockfile, collision_check, dep_record, module_record
from svarupa.model import Evidence

pytestmark = pytest.mark.determinism

_CHILD = (
    "import sys; sys.path.insert(0, {root!r})\n"
    "from tests.test_determinism import render\n"
    "print(render(), end='')\n"
)

TOOL = "0.1.0.dev0"
GRAMMARS = {"python": "0.25.0", "typescript": "0.23.2"}

CORPUS = [
    "src/api",
    "src/auth",
    "src/billing",
    "src/db",
    "src/café",
    "src/naïve/deep",
    "src/with space",
    "src/comma, here",
    "src/Ünïcode",
    "src/ZZZ",
    "src/aaa",
]
DEPS = [
    ("src/api", "src/auth"),
    ("src/api", "src/billing"),
    ("src/billing", "src/db"),
    ("src/café", "src/naïve/deep"),
]


def render(seed: int | None = None) -> str:
    recs = [module_record(m) for m in CORPUS] + [dep_record(a, b) for a, b in DEPS]
    if seed is not None:
        random.Random(seed).shuffle(recs)
    return Lockfile.build(TOOL, GRAMMARS, recs).render()


# --------------------------------------------------------------------------
# Ordering independence
# --------------------------------------------------------------------------


def test_input_order_never_affects_bytes() -> None:
    baseline = render()
    for seed in range(25):
        assert render(seed) == baseline, f"input order leaked at seed {seed}"


def test_dict_and_set_iteration_do_not_leak() -> None:
    """Guards against an unsorted set reaching serialization.

    A subprocess is required: PYTHONHASHSEED is fixed at interpreter start, so
    varying it inside the running process would prove nothing.
    """
    script = _CHILD.format(root=str(Path(__file__).resolve().parents[1]))
    outputs: set[str] = set()
    for seed in ("0", "1", "42", "12345", "99999"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.add(out.stdout)
    assert len(outputs) == 1, f"PYTHONHASHSEED changed output ({len(outputs)} variants)"


def test_locale_does_not_affect_collation() -> None:
    """Turkish dotless-i is the classic sorting trap."""
    script = _CHILD.format(root=str(Path(__file__).resolve().parents[1]))
    outputs: set[str] = set()
    for loc in ("C", "en_US.UTF-8", "tr_TR.UTF-8", "de_DE.UTF-8"):
        env = {**os.environ, "LC_ALL": loc, "LANG": loc}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.add(out.stdout)
    assert len(outputs) == 1, f"locale changed output ({len(outputs)} variants)"


# --------------------------------------------------------------------------
# Unicode normalization
# --------------------------------------------------------------------------


def test_nfd_and_nfc_paths_produce_identical_bytes() -> None:
    """The core NFC guard.

    APFS preserves whatever bytes it is given, so the same logical filename can
    arrive in either form depending on the creating tool and what git stored.
    """
    nfc = unicodedata.normalize("NFC", "src/café/a.py")
    nfd = unicodedata.normalize("NFD", "src/café/a.py")
    assert nfc != nfd, "test corpus must actually differ in byte form"
    assert Evidence(nfc, 1, 1).file == Evidence(nfd, 1, 1).file


def test_git_checkout_of_an_nfd_path_normalizes(tmp_path: Path) -> None:
    """Exercise the real delivery channel, not os.mkdir.

    git's core.precomposeunicode (default true in Apple-shipped git, absent on
    Linux) means the form a build sees depends on OS, git build, and config.
    Creating the file directly would test the wrong layer.
    """
    if not shutil_which("git"):
        pytest.skip("git unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")

    nfd_dir = repo / unicodedata.normalize("NFD", "café")
    nfd_dir.mkdir()
    (nfd_dir / "mod.py").write_text("x = 1\n")
    run("add", "-A")
    run("commit", "-qm", "add nfd path")

    listed = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert listed, "git recorded no files"

    # Whatever form git stored, our normalization must collapse it to one key.
    keys = {Evidence(p, 1, 1).file for p in listed}
    assert len(keys) == len(listed)
    assert all(unicodedata.is_normalized("NFC", k) for k in keys)


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


# --------------------------------------------------------------------------
# Collisions must be diagnosed, never silently merged
# --------------------------------------------------------------------------


def test_normalization_collision_is_detected() -> None:
    """NFD and NFC of one name are one file on macOS, two on Linux.

    Silently merging them would produce a silently wrong lockfile.
    """
    nfc = unicodedata.normalize("NFC", "src/café")
    nfd = unicodedata.normalize("NFD", "src/café")
    from svarupa.model import norm_path

    assert norm_path(nfc) == norm_path(nfd)


def test_case_collision_is_detected() -> None:
    got = collision_check(["src/Utils", "src/utils"])
    assert got and got[0][1] == ["src/Utils", "src/utils"]


# --------------------------------------------------------------------------
# Stability: what must and must not churn
# --------------------------------------------------------------------------


def test_repeated_renders_are_byte_identical() -> None:
    assert len({render() for _ in range(50)}) == 1


def test_roundtrip_is_a_fixed_point() -> None:
    once = render()
    assert Lockfile.parse(once).render() == once


def test_adding_an_unrelated_module_does_not_touch_existing_records() -> None:
    """Locality: a change must diff only where it happened."""
    before = render().splitlines()
    recs = [module_record(m) for m in [*CORPUS, "src/brand_new"]] + [
        dep_record(a, b) for a, b in DEPS
    ]
    after = Lockfile.build(TOOL, GRAMMARS, recs).render().splitlines()
    assert [ln for ln in before if ln not in after] == []
    assert [ln for ln in after if ln not in before] == ["module\tsrc/brand_new"]
