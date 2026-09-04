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
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from svarupa.lock import Lockfile, collision_check, dep_record, module_record
from svarupa.model import Evidence, norm_path

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
    """Live on glibc, inert on macOS, which is where it is usually run.

    BSD libc collation ignores `LC_COLLATE`: measured, `setlocale(LC_ALL, "")`
    under `tr_TR.UTF-8` sorts `I`, `i` and dotted-I identically to `C`. So on a
    developer laptop this test cannot fail, and its real execution happens on
    the Ubuntu CI leg. Recorded rather than left implicit, because a promoted
    decision says a platform-conditional test is unverified until CI proves it.
    """
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

    Three traps this must avoid, all of which made the first version of this
    test pass even with `norm_path` as the identity function:

    1. `git ls-files` with the default `core.quotepath=true` octal-mangles
       non-ASCII paths into a pure-ASCII quoted string, so the NFD form never
       reaches the assertion at all.
    2. Apple-shipped git sets `core.precomposeunicode=true`, so on macOS git
       hands back NFC and there is nothing left to normalize. That is a real
       platform fact, so we skip with a reason rather than assert falsely --
       the CI matrix then records which platform actually exercised this.
    3. With a single file, `len(keys) == len(listed)` is `1 == 1` and can
       never fail.
    """
    if not shutil.which("git"):
        pytest.skip("git unavailable")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=repo, capture_output=True, text=True, check=True
        ).stdout

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    # Do not let git rewrite the bytes we are trying to test.
    git("config", "core.precomposeunicode", "false")
    git("config", "core.quotepath", "false")

    nfd_dir = repo / unicodedata.normalize("NFD", "café")
    nfd_dir.mkdir()
    (nfd_dir / "mod.py").write_text("x = 1\n")
    (nfd_dir / "other.py").write_text("y = 2\n")
    plain = repo / "plain"
    plain.mkdir()
    (plain / "z.py").write_text("z = 3\n")

    git("add", "-A")
    git("commit", "-qm", "add nfd path")

    listed = [x for x in git("ls-files", "-z").split("\0") if x]
    assert len(listed) == 3, f"expected 3 tracked files, got {listed}"

    non_nfc = [p for p in listed if not unicodedata.is_normalized("NFC", p)]
    if not non_nfc:
        pytest.skip(
            "this git build normalized the path to NFC on write "
            "(core.precomposeunicode behaviour); nothing left to normalize here"
        )

    # The point of the test: a non-NFC path came back, and our guard fixes it.
    for raw in non_nfc:
        assert unicodedata.is_normalized("NFC", norm_path(raw))
        assert norm_path(raw) != raw

    keys = {norm_path(p) for p in listed}
    assert len(keys) == 3, "normalization must not collapse distinct files"


# --------------------------------------------------------------------------
# Collisions must be diagnosed, never silently merged
# --------------------------------------------------------------------------


def test_normalization_collision_is_diagnosed_not_silently_merged() -> None:
    """The previous version of this test asserted the bug it claimed to catch.

    It checked `norm_path(nfc) == norm_path(nfd)` -- i.e. it asserted the
    silent merge that design 7.1 explicitly forbids -- under a name claiming
    detection. `collision_check` must be given the RAW ids, because once
    `norm_path` has run the pre-images are gone.
    """
    nfc = unicodedata.normalize("NFC", "src/café")
    nfd = unicodedata.normalize("NFD", "src/café")
    assert nfc != nfd

    diags = collision_check([nfc, nfd])
    assert diags, "NFC/NFD collision must be diagnosed, never silently merged"
    assert diags[0].code == "SVA-L-004"
    assert "normalization" in diags[0].message.lower()


def test_case_and_normalization_are_reported_as_separate_axes() -> None:
    """The remedy differs, so the diagnostics must be distinguishable."""
    nfc = unicodedata.normalize("NFC", "src/café")
    nfd = unicodedata.normalize("NFD", "src/café")
    diags = collision_check([nfc, nfd, "src/Utils", "src/utils"])
    msgs = " ".join(d.message.lower() for d in diags)
    assert len(diags) == 2
    assert "normalization" in msgs and "case" in msgs


def test_case_collision_is_detected() -> None:
    got = collision_check(["src/Utils", "src/utils"])
    assert len(got) == 1
    assert got[0].code == "SVA-L-004"
    assert "src/Utils" in got[0].subject and "src/utils" in got[0].subject


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
