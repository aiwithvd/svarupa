#!/usr/bin/env bash
# Release svarupa in two steps. main is protected, so a release goes through
# a pull request like any other change.
#
#   scripts/release.sh prepare [VERSION]
#       Compute the next YYYY.M.N version (or take VERSION), bump it on a
#       release/vVERSION branch, draft docs/releases/vVERSION.md, run every
#       check, and open the release PR. Edit the notes in the PR, then merge.
#
#   scripts/release.sh publish
#       After the release PR is merged: tag main, let the release workflow
#       publish to PyPI (trusted publishing, no token), create the GitHub
#       Release from the notes file, verify a clean install, and open a PR
#       that moves the dogfood CI pin to the new version.
#
# Needs: git, gh (logged in), uv, curl, python3. Run from anywhere inside the
# repository.

set -euo pipefail

REPO="aiwithvd/svarupa"
PACKAGE="svarupa"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

need() {
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed"
  done
}

pkg_version() {
  python3 - <<'EOF'
import re
text = open("pyproject.toml", encoding="utf8").read()
print(re.search(r'(?m)^version = "([^"]+)"$', text).group(1))
EOF
}

# On main, clean, and identical to origin/main. Everything a release does is
# measured against that commit, so any local difference would be released by
# accident or lost.
require_clean_main() {
  [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "switch to main first (git switch main)"
  [ -z "$(git status --porcelain)" ] || die "the working tree has changes; commit or stash them"
  git fetch --quiet --tags origin
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
    || die "main differs from origin/main; run: git pull --ff-only"
}

# The newest ci run on HEAD must have succeeded. If it is still running, wait.
require_green_ci() {
  local sha run_id conclusion
  sha="$(git rev-parse HEAD)"
  run_id="$(gh run list --repo "$REPO" --workflow ci --commit "$sha" --limit 1 \
    --json databaseId -q '.[0].databaseId // empty')"
  [ -n "$run_id" ] || die "no ci run found for $sha; push it and wait for CI"
  say "waiting for ci run $run_id on ${sha:0:7}"
  gh run watch "$run_id" --repo "$REPO" --exit-status >/dev/null 2>&1 || true
  conclusion="$(gh run view "$run_id" --repo "$REPO" --json conclusion -q .conclusion)"
  [ "$conclusion" = "success" ] || die "ci on ${sha:0:7} is '$conclusion'; fix it before releasing"
}

run_checks() {
  say "running checks"
  uv run ruff check svarupa tests
  uv run ruff format --check svarupa tests
  uv run pyright svarupa
  uv run pytest -q
}

prepare() {
  need git gh uv python3
  require_clean_main
  require_green_ci

  local version notes branch prev
  if [ $# -ge 1 ]; then
    version="$1"
  else
    version="$(uv run --quiet python scripts/release_version.py next)"
  fi
  uv run --quiet python scripts/release_version.py check "$version"
  git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "tag v$version already exists"

  local last
  last="$(git describe --tags --abbrev=0 HEAD 2>/dev/null || true)"
  if [ -n "$last" ] && [ "$(git rev-list --count "$last..HEAD")" -eq 0 ]; then
    die "nothing to release: main has no commits since $last"
  fi

  prev="$(pkg_version)"
  branch="release/v$version"
  say "preparing $version (current $prev) on $branch"
  git switch --quiet -c "$branch"

  # Exact string replace of the one `version = "..."` line; sed would treat
  # the dots in the old version as regex wildcards.
  python3 - "$prev" "$version" <<'EOF'
import sys
path, old, new = "pyproject.toml", sys.argv[1], sys.argv[2]
text = open(path, encoding="utf8").read()
line = f'version = "{old}"\n'
assert text.count(line) == 1, f"expected exactly one {line!r} in {path}"
open(path, "w", encoding="utf8").write(text.replace(line, f'version = "{new}"\n'))
EOF
  [ "$(pkg_version)" = "$version" ] || die "could not bump the version in pyproject.toml"
  uv lock --quiet
  # Our own lockfile header carries the tool version.
  uv run --quiet svarupa . --lock >/dev/null

  notes="$(uv run --quiet python scripts/release_notes.py "$version")"
  run_checks

  git add pyproject.toml uv.lock .svarupa/architecture.lock "$notes"
  git commit --quiet -m "Release $version"
  git push --quiet -u origin "$branch"
  gh pr create --repo "$REPO" --base main --head "$branch" \
    --title "Release $version" --body-file "$notes" >/dev/null

  say "release PR opened: $(gh pr view "$branch" --repo "$REPO" --json url -q .url)"
  cat <<EOF

Next:
  1. Edit $notes in the PR: rewrite bullets for users and resolve every TODO.
  2. Merge the PR once CI is green.
  3. git switch main && git pull --ff-only && scripts/release.sh publish
EOF
}

# The release workflow run for a tag appears a few seconds after the push.
wait_for_release_run() {
  local tag="$1" run_id="" i
  for i in $(seq 1 30); do
    run_id="$(gh run list --repo "$REPO" --workflow release --branch "$tag" --limit 1 \
      --json databaseId -q '.[0].databaseId // empty')"
    [ -n "$run_id" ] && break
    sleep 5
  done
  [ -n "$run_id" ] || die "no release workflow run started for $tag"
  echo "$run_id"
}

wait_for_pypi() {
  local version="$1" i
  for i in $(seq 1 60); do
    if curl -fsS -o /dev/null "https://pypi.org/pypi/$PACKAGE/$version/json"; then
      return 0
    fi
    sleep 10
  done
  die "$PACKAGE $version did not appear on PyPI within 10 minutes"
}

publish() {
  need git gh uv curl python3
  require_clean_main

  local version tag notes run_id dir
  version="$(pkg_version)"
  uv run --quiet python scripts/release_version.py check "$version" \
    || die "pyproject version $version is not a release version; run prepare first"
  tag="v$version"
  notes="docs/releases/$tag.md"
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "$tag already exists"
  [ -f "$notes" ] || die "$notes is missing; was the release PR merged?"
  if grep -q 'TODO:' "$notes"; then
    die "$notes still has TODO markers; finish the notes in a PR first"
  fi
  require_green_ci

  say "tagging $tag"
  git tag -a "$tag" -m "$PACKAGE $version"
  git push --quiet origin "$tag"

  run_id="$(wait_for_release_run "$tag")"
  say "waiting for release workflow run $run_id (tests, build, publish)"
  if ! gh run watch "$run_id" --repo "$REPO" --exit-status >/dev/null 2>&1; then
    if gh run view "$run_id" --repo "$REPO" --log-failed 2>/dev/null | grep -q invalid-publisher; then
      die "PyPI has no trusted publisher for this repository. On pypi.org open
  your project 'svarupa' > Publishing and add a GitHub publisher:
    owner: aiwithvd  repository: svarupa  workflow: release.yml  environment: pypi
  Then re-run: gh run rerun $run_id --repo $REPO --failed
  and finish with: scripts/release.sh publish-finish"
    fi
    die "release workflow failed: gh run view $run_id --repo $REPO --log-failed"
  fi

  finish "$version"
}

# Everything after PyPI has the package. Split out so a release whose
# workflow was re-run by hand can be completed.
finish() {
  local version="$1" tag="v$1" dir installed
  say "waiting for $PACKAGE $version on PyPI"
  wait_for_pypi "$version"

  say "creating the GitHub Release with the files from PyPI"
  dir="$(mktemp -d)"
  curl -fsS "https://pypi.org/pypi/$PACKAGE/$version/json" \
    | python3 -c 'import json, sys; [print(u["url"]) for u in json.load(sys.stdin)["urls"]]' \
    | while read -r url; do curl -fsS -o "$dir/${url##*/}" "$url"; done
  gh release create "$tag" --repo "$REPO" --title "$tag" \
    --notes-file "docs/releases/$tag.md" --latest "$dir"/*

  say "verifying a clean install"
  installed="$(uvx --refresh --from "$PACKAGE==$version" "$PACKAGE" --version)"
  [ "$installed" = "$PACKAGE $version" ] || die "clean install reports '$installed'"

  say "opening a PR that pins the dogfood workflow to $version"
  git switch --quiet -c "chore/pin-$tag"
  uv run --quiet svarupa setup ci_github --force >/dev/null
  if [ -n "$(git status --porcelain)" ]; then
    git commit --quiet -am "Pin the architecture workflow to svarupa==$version"
    git push --quiet -u origin "chore/pin-$tag"
    gh pr create --repo "$REPO" --base main --head "chore/pin-$tag" \
      --title "Pin the architecture workflow to svarupa==$version" \
      --body "Moves \`.github/workflows/svarupa.yml\` to the version just released." >/dev/null
  fi
  git switch --quiet main

  say "released $PACKAGE $version"
  echo "  PyPI:   https://pypi.org/project/$PACKAGE/$version/"
  echo "  GitHub: https://github.com/$REPO/releases/tag/$tag"
}

case "${1:-}" in
  prepare) shift; prepare "$@" ;;
  publish) publish ;;
  publish-finish)
    need git gh uv curl python3
    require_clean_main
    finish "$(pkg_version)"
    ;;
  *) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
