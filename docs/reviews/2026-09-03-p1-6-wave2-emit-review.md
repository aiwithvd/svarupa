# Review #9: P1-6 wave 2 (emit: viewer, JSON, REPORT.md)

**Date:** 2026-09-03
**Reviewer:** Fable (adversarial)
**Reviewed at:** `58819fd` · **Fixes at:** `6acf770`
**Verdict:** *"Does not meet its spec. The wave's headline claim, 'escaping is a property of the types, not of discipline', is false as written."*

---

## The finding that is about process, not code

The wave was named for a security property and the property was not there.

`markup.py` claimed, and the commit message repeated, that "under pyright
strict, dropping a bare `str` where `Markup` is expected is a type error." But
`tag(name, body: object)` and `join(parts: object)` both accept `object`, and a
`str` is an `object`. Verified:

```
$ ./.venv/bin/pyright /tmp/hole.py          # tag("p", hostile_str)
0 errors, 0 warnings, 0 informations
$ ./.venv/bin/python /tmp/hole.py
<p class="x"><img src=x onerror=alert(1)></p>
<img src=x onerror=alert(1)>
```

No live call site did this, so every runtime test was green. That is the trap:
the tests passed because of *discipline plus a runtime backstop*, which is
precisely what the type-level claim was supposed to replace. The next component
writing `tag("text", node.label)` would have compiled clean and injected.

There was even a tell I wrote myself and ignored: `join` carried a
`# type: ignore[union-attr]`. A type-ignore on a security boundary is a
signature admitting it is wrong.

The generalisation: **a type-level guarantee is only as strong as the narrowest
type on its boundary.** Widening a security-critical parameter for caller
convenience erases the invariant while leaving the docstring that claims it.
This is the "docstring arguing a check is unnecessary" decision applied to a
signature: the check the prose describes has to be the check the type expresses.

**And the registry lied in nine more places than the reviewer found.** Review #9
named `SVA-R-002` and `SVA-R-004`. Writing a drift checker to fix those two
turned up **eleven** descriptions sharing no content word with the message they
label, including the entire `SVA-B-*` series, off by several positions:
`SVA-B-001` was documented as "an element reached the graph without evidence"
while emitting "two different nodes claim the same id", and that text described
`SVA-B-007`. I wrote them from recall instead of reading the emission sites,
one wave after promoting a decision about exactly that, and one wave after
correcting the `SVA-D-*` series for the same reason. Correcting instances by
hand is not a discipline; the check is.

---

## Findings and triage

| # | Finding | Rank | Disposition | Why |
|---|---|---|---|---|
| F1 | The escaping guarantee is enforced by discipline, not by the types | MUST-FIX | **Accepted, fixed** | Detailed above. `body: Markup`, `parts: Iterable[Markup]`, and the `# type: ignore` gone. Narrowing caught three real call sites. `tests/test_markup_types.py` runs pyright and asserts it complains, because nothing else can distinguish "the types enforce this" from "no call site violates it yet" |
| F2 | Registry descriptions `SVA-R-002` and `SVA-R-004` are wrong | MUST-FIX | **Accepted, fixed, and widened** | The reviewer found two; a drift checker found eleven. Both directions of the meta-test existed and neither could catch a description that is about something else. Now the test parses the literal `message=` at each emission site and requires a shared content word, with genuinely dynamic codes listed by name so a *new* literal-less code fails rather than being exempted |
| F3 | The evidence link builds a `javascript:`-scheme URL from repository text | SHOULD-FIX | **Accepted, fixed** | Reachable, and non-executable only because the appended `#L<line>` failed to parse. An accident of an unrelated suffix is not a control. Scheme allow-listed at the sink, tested under node against the guard extracted from the shipped artifact, and verified in a real browser. The viewer docstring claimed no repository text is handled in the browser, which this falsified; corrected to say what is true |
| F4 | The output directory is never cleared, so stale artifacts survive | SHOULD-FIX | **Accepted, fixed** | Demonstrated: a `diagrams/erd.json` from a prior run stayed byte-for-byte after a run that did not produce it. Now owned via a marker and cleared to a declared set. A directory holding files this tool does not own is **refused**, not emptied: `--out` takes an arbitrary path and a typo should not be destructive. Claimed before the analysis, since a refusal after minutes of scanning arrives under a page of output that read as success |
| F5 | `json_script` is fully tested dead code | SHOULD-FIX | **Accepted, deleted** | In `__all__`, two tests, two mutation entries, no production caller. Its mutations were padding the caught count with guarantees no artifact depended on. Deleted rather than parked: P2 can add it back when something calls it |
| F6 | `_first_problem` attributes withheld reasons by substring | CONSIDER | **Accepted, fixed** | A search for `/spec/root` matched `/spec/root/api`, so a parent view could be given its child's failure as its reason. Equality now. Promoted from CONSIDER because it is a wrong claim in a report, which is the failure class this product exists to prevent |
| F7 | Exit code 2 collides with argparse and diverges from the tool's own code | CONSIDER | **Accepted, fixed** | A structured refusal was indistinguishable by exit code from a mistyped flag. One non-zero code now means "no usable answer"; 2 stays argparse's |
| S1 | §6.2 items absent without disclosure | Spec scope | **Accepted, recorded** | The reviewer was right that only the graph explorer was previously scoped out. The section now names the phase for the in-viewer Overview, the staleness header, `--bundle` and `refinements.yaml`, and states that anything in it not on that list is either built or a bug. The SVG inversion is written into the paragraph it contradicts |

**Nothing rejected.** The reviewer also checked and cleared several things:
the `produced.items()` versus `sorted(produced)` iteration (layout results are
keyed by kind and every byte-producing reader re-sorts, so order cannot reach
the output), server-side attribute and SVG escaping, the cross-process
determinism tests, and `detect`'s refusal for sockets, fifos and dangling
symlinks.

---

## What the mutation suite could not see

All 12 mutations were caught, which was true and beside the point. The gaps
were the mutations **not on the list**:

* no mutation widened `tag` back to `object`, which is F1 in one line;
* no mutation touched a registry description, which is F2;
* nothing exercised the URL sink, the output directory, or the withheld
  attribution.

A mutation suite proves what it enumerates. Its clean run is evidence about the
list, not about the component, and a list written by the same person who wrote
the code inherits that person's blind spots. The list now has 15 entries
including `tag`-widening, scheme-guard removal, clear-skipping, refusal-skipping
and the substring regression.

---

## Promoted to the decision log

1. **A type-level guarantee is only as strong as the narrowest type on its boundary** (F1). Widening a security-critical parameter for caller convenience erases the invariant and leaves the docstring claiming it. A `# type: ignore` on such a boundary is the signature admitting it is wrong.
2. **A guarantee the type checker is supposed to provide needs a test that the type checker complains** (F1). Runtime tests cannot distinguish enforcement from the absence of a violating call site.
3. **A free-text label describing machine behaviour must be checked against that behaviour** (F2). A registry checked only for existence and contiguity is a set of claims no test constrains, and it rots exactly where it just rotted.
4. **HTML escaping is the wrong escaping for a URL** (F3). Wherever repository text reaches `href`, `src` or `location`, the scheme is allow-listed at the sink; a mitigation that works only because of an unrelated suffix is not a control.
5. **An artifact must be a pure function of the run that wrote it** (F4). Writing into a directory without owning its whole contents makes the output a function of run history, which is the cross-run nondeterminism the byte-identity work exists to remove. Ownership is declared and marked, never assumed, because the alternative is a destructive typo.
6. **Liveness comes from the import graph, not from having tests** (F5). A tested-but-uncalled export certifies dead code as alive and pads a mutation count with guarantees nothing depends on.
7. **`in` on identifiers that share a namespace prefix is a false-match generator** (F6). When one id can be a prefix of another, match on equality over a parsed field.
8. **A mutation suite proves what it enumerates** (this review). Its clean run is evidence about the list, not the component, and a list written by the author inherits the author's blind spots.

---

## Measured after

```
465 passed, 1 skipped, 2 xfailed; ruff clean; pyright strict 0 errors
15 mutations, all caught
browser: javascript:-scheme citation renders as a span, 0 anchors, no global set,
         path still visible; ordinary citations still resolve to working links
```

---

## Riskiest remaining untested assumption

The reviewer's answer was that every present and future call site wraps
repository text before handing it to `tag`/`join`. F1 removes that: it is now a
type error, asserted by a test that runs pyright.

What replaces it is narrower and harder. **That `esc`'s five characters are
sufficient for every context the emitter writes into.** They are correct for
element text and for quoted attributes, which is all this stage produces today.
They are *not* sufficient for a CSS context, a URL context (F3 was exactly
that), or an unquoted attribute, and nothing in the type system distinguishes
those contexts: `Markup` means "escaped for HTML", and a future emitter that
interpolates `Markup` into a `style` attribute or a `url()` would type-check
clean and be wrong. The next stage that writes into a new context needs its own
escaping function, not this one.
