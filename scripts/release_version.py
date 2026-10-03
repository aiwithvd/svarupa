"""Calendar version for the next release: YYYY.M.N.

Year and month come from the UTC date. N counts releases within that month,
starting at 1. No zero padding: PEP 440 normalizes `2026.01.1` to `2026.1.1`,
so a padded tag would not match the version PyPI shows.

N is one past the highest N found in either the git tags or the versions
already on PyPI. Checking PyPI too means a manual upload without a tag can
never be overwritten by a computed version (PyPI refuses re-uploads anyway,
but only after the release PR is merged).

Usage:
    release_version.py next              print the next version
    release_version.py check VERSION     exit 1 unless VERSION is valid
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from datetime import date, datetime, timezone

PACKAGE = "svarupa"
_VALID = re.compile(r"^(?P<y>[0-9]{4})\.(?P<m>[1-9]|1[0-2])\.(?P<n>[1-9][0-9]*)$")


def validate(version: str) -> str:
    if not _VALID.match(version):
        raise ValueError(
            f"{version!r} is not YYYY.M.N (year, month without zero padding, "
            "release number from 1), e.g. 2026.10.1"
        )
    return version


def next_version(today: date, tags: Iterable[str], published: Iterable[str]) -> str:
    prefix = f"{today.year}.{today.month}."
    highest = 0
    for raw in (*tags, *published):
        v = raw.removeprefix("v")
        m = _VALID.match(v)
        if m and v.startswith(prefix):
            highest = max(highest, int(m["n"]))
    return f"{prefix}{highest + 1}"


def _git_tags() -> list[str]:
    out = subprocess.run(["git", "tag", "--list"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def _pypi_versions() -> list[str]:
    url = f"https://pypi.org/pypi/{PACKAGE}/json"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return list(data.get("releases", {}))


def main(argv: list[str]) -> int:
    if argv[:1] == ["next"] and len(argv) == 1:
        today = datetime.now(timezone.utc).date()
        print(next_version(today, _git_tags(), _pypi_versions()))
        return 0
    if argv[:1] == ["check"] and len(argv) == 2:
        try:
            validate(argv[1])
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
