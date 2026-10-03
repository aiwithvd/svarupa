# Calendar versioning: YYYY.M.N

Releases after 0.2.2 are versioned `YYYY.M.N`: year, month without zero
padding, and a release counter within that month starting at 1 (`2026.10.1`,
`2026.10.2`, then `2026.11.1`). The same scheme as OpenClaw. We release often and
in small steps, and a date says more about a release than a semver number that
nobody bumps consistently. The format that needs compatibility guarantees, the
lockfile, already carries its own `# schema M.m` stamp, and that stamp, not the
package version, decides whether two lockfiles can be compared.

## Consequences

- PyPI keeps every version forever and `2026.10.1` sorts above `0.2.2`, so the
  switch cannot be undone without yanking releases.
- No zero padding: PEP 440 normalizes `2026.01.1` to `2026.1.1`, and a padded
  git tag would then not match the version on PyPI.
- N is computed from both git tags and PyPI, so a manual upload without a tag
  is never overwritten (`scripts/release_version.py`).
- A breaking change in CLI flags or output is no longer signalled by the
  version number; it has to be stated in the release notes' Upgrading section.

## Considered options

- **Semver (0.x.y):** what we had. Rejected: the major/minor/patch split was
  never applied consistently, and breaking-change signalling already lives in
  the lockfile schema stamp.
- **`YYYY.M.D`:** reads as a date, but a second release on the same day needs
  a fourth segment.
- **`YY.N.P` (pip):** keeps a patch slot, but is less obviously a date.
