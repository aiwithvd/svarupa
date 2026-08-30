# Architecture Lockfile Grammar

**Schema 1.0** · `.svarupa/architecture.lock` · committed to your repository

This file is the deterministic fingerprint of your system's architecture. It is
designed to be **read and diffed by humans in a pull request**, before CI even
runs.

```
# svarupa 0.1.0
# schema 1.0
# grammars python@0.25.0 typescript@0.23.2
module	src/api
module	src/auth
module	src/billing
dep	src/api	src/auth
dep	src/api	src/billing
dep	src/billing	src/auth
```

Adding one dependency adds **exactly one line**:

```diff
  dep	src/api	src/billing
+ dep	src/billing	src/auth
```

---

## Grammar

```
lockfile   := header* record*
header     := "#" SP text NEWLINE
record     := kind (TAB field)* NEWLINE
kind       := [a-z_]+
field      := escaped-text
NEWLINE    := LF          ; U+000A only
```

- Fields are separated by a literal **TAB** (`U+0009`).
- Blank lines are ignored.
- Lines beginning `#` are header or comment.
- A single trailing CR is stripped, so a CRLF file still parses.

### NEWLINE means LF, and only LF

Not "whatever the host language calls a line break." Python's
`str.splitlines()`, for instance, also breaks on VT (`U+000B`), FF (`U+000C`),
NEL (`U+0085`), LS (`U+2028`) and PS (`U+2029`) — **all of which are legal in a
POSIX filename**. Splitting on them would turn one module record into a module
plus a phantom opaque "fact", silently, in the file you commit.

### The parser enforces this grammar

A line whose kind does not match `[a-z_]+` is **rejected**, not tolerated. This
is what stops an unresolved merge conflict from becoming architecture:

```
<<<<<<< HEAD          → SVA-L-001, refused
=======               → SVA-L-001, refused
```

Known kinds are also checked for **arity**. `dep\tsrc/api` (one field) is
refused with `SVA-L-002` rather than silently parsed as a different fact than
the truncated line intended.

Forward compatibility is unaffected: a *well-formed* unknown kind
(`quantum_widget\tx\ty`) is still retained verbatim. The tolerance exists for
future records, not as an amnesty for corruption.

### Why tab

POSIX paths may legally contain spaces, commas, and even the string `" -> "`.
A space- or comma-delimited format cannot represent `src/my project, v2/a -> b`
without ambiguity. Tab cannot appear unescaped in a path or an endpoint
template, so it is the only separator that needs no quoting rules.

### Escaping

Within a field:

| Character | Escaped as |
|---|---|
| `\` (U+005C) | `\\` |
| TAB (U+0009) | `\t` |
| LF (U+000A) | `\n` |
| CR (U+000D) | `\r` |

Backslash is escaped first, so escapes are never doubly-applied. Every other
byte is emitted verbatim, including non-ASCII. Paths are **NFC-normalized**
before serialization.

`\\`, `\t`, `\n` and `\r` are the **only** valid escapes. An unrecognized
sequence such as `\q` is refused with `SVA-L-004`. Tolerating it would make
parse→render non-idempotent: it would decode to a literal backslash-q and
re-encode as `\\q`, changing the bytes of a committed file with no diagnostic.

---

## Header

```
# svarupa <tool-version>
# schema <major>.<minor>
# grammars <lang>@<version> [<lang>@<version> ...]
```

Grammar versions are recorded because a tree-sitter grammar patch release can
change node structure and therefore extraction output. They are `==` pinned in
the package metadata for the same reason.

**The `# schema` stamp is mandatory.** A lockfile without one refuses to diff,
rather than being assumed to match the current schema. A missing stamp is
strictly less trustworthy than a mismatched one, and defaulting would
manufacture provenance for a file that has none.

---

## Record kinds

Schema 1.0 defines:

| Kind | Fields | Meaning |
|---|---|---|
| `module` | `<id>` | A structural module: a directory, package, or workspace member |
| `dep` | `<from>` `<to>` | Module `from` depends on module `to` |

Reserved for later minor versions (parsers must already tolerate them):

| Kind | Fields |
|---|---|
| `endpoint` | `<method-and-path>` `<handler-module>` |
| `datastore` | `<name>` |
| `service` | `<name>` |
| `queue` | `<name>` |
| `surface` | `<module>` `<exported-symbol>` |

---

## What is deliberately absent

### Line numbers

Evidence lives in `graph.json`, which is regenerated and gitignored, never in
the lockfile.

Line numbers are the most volatile data in the system. A record reading
`billing/refunds.py:44-71` would churn on any edit above line 44, including a
new import or a docstring, directly contradicting the stability the lockfile
exists to provide. They also buy nothing: the diff engine recovers them from
the freshly built head graph, so the PR comment still shows exact locations.

### Aggregated lists

Never `module api -> auth, billing`. That form renders an added dependency as
one removed line plus one added line, and asks the reviewer to eyeball-diff a
comma list. One fact per line is the entire point.

### Anything derived from clustering

Community detection is chaotically sensitive to input perturbation. Measured on
real repositories: a single added import flips 25-38% of community assignments.
Determinism means *same input, same output* — and a pull request changes the
input. Community ids therefore never appear here. Module identity comes from
structure that developers declare: directories, packages, and workspace
members, which change only when a file actually moves.

### Model output

No text produced by a language model reaches this file. Narrative and naming
live in `.svarupa/refinements.yaml`.

---

## Determinism contract

The same commit must produce byte-identical bytes on any machine.

- Every collection sorted by **codepoint**, never locale collation
- Paths repo-relative, posix separators, NFC-normalized
- No timestamps, no absolute paths, no iteration-order dependence
- Records deduplicated, then sorted by `(kind, fields...)`

Verified in CI on Linux and macOS across Python 3.10-3.13, with
`PYTHONHASHSEED` varied and `LC_ALL=C`, including a git-checkout fixture
carrying an NFD-encoded path.

### Collisions

Two module ids that are distinct on disk but identical after NFC normalization,
or after case-folding on a case-insensitive filesystem, raise `SVA-L-007`. They
are never silently merged, because a silently merged module is a silently wrong
lockfile.

The two axes are checked **separately and reported separately**, because the
remedy differs:

| Axis | Example | Why it matters |
|---|---|---|
| Normalization | `café` (NFC) vs `café` (NFD) | Two files on Linux, one on macOS |
| Case | `src/Utils` vs `src/utils` | Two files on Linux, one on APFS or NTFS |

Note that case-folding alone does **not** catch the normalization axis, because
`str.casefold()` does not normalize. Detection must also run on the **raw**
ids: once paths are normalized, the pre-images are gone and there is nothing
left to compare.

---

## Evolution policy

This is what stops a future release becoming a flag day for everyone who
already adopted the tool.

### Additive change → minor bump, still diffs

Adding a new record kind bumps the **minor** version. **Parsers must retain
unknown kinds verbatim** and diff them as opaque additions and removals. An
older build can therefore still diff a newer lockfile.

Dropping unknown lines instead would silently report deletions that never
happened, which is worse than failing.

### Breaking change → major bump, refuses

Changing the field shape of an existing kind bumps the **major** version.
Diffing across a major mismatch refuses with an actionable message:

```
lockfile schema 1.x cannot be diffed against 2.x.
Regenerate the base lockfile with this version of svarupa.
```

Hard refusal is reserved for this case alone.
