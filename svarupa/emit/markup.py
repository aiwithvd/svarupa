"""Escaping, made structural rather than a matter of discipline.

Everything this stage writes into a document comes from a repository: module
names, file paths, labels. POSIX allows almost every byte in a filename, so a
directory named `</script><img src=x onerror=alert(1)>` is scannable, and the
artifact is opened in a browser by the person who ran the tool. Getting one
interpolation wrong is enough.

So the type system carries the invariant. `Markup` is the only thing a document
accepts, and the only ways to make one are `esc` (which escapes) and `raw`
(which announces in one word that it does not). A bare `str` handed to `tag` or
`join` is a pyright error, which turns "someone forgot to escape" from a review
miss into a failed build.

**That claim was false when first written**, and the correction is the reason
these signatures are as narrow as they are. `tag(name, body: object)` and
`join(parts: object)` accepted a plain `str` silently and spliced it in
verbatim, so the guarantee rested on every call site remembering `esc`, which
is exactly the discipline the types were supposed to replace. Measured:
`tag("p", hostile_str)` type-checked clean and emitted
`<p><img src=x onerror=alert(1)></p>`. A type-level guarantee is only as strong
as the narrowest type on its boundary, and widening a security-critical
parameter for caller convenience erases the invariant while leaving the
docstring that claims it.

`tests/test_emit.py` still attacks the property at runtime with names a real
filesystem accepts, and `tests/test_markup_types.py` asserts the type error
itself, because a guarantee the type checker is supposed to provide needs a
test that the type checker actually complains.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NewType

__all__ = [
    "EMPTY",
    "Markup",
    "attrs",
    "esc",
    "join",
    "raw",
    "tag",
]

Markup = NewType("Markup", str)

# `&` first: escaping it after the others would double-escape their output.
_HTML = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)


def esc(text: object) -> Markup:
    """The only way to put repository-derived text into a document.

    `'` is escaped as well as `"`, because an attribute value written with
    single quotes is otherwise breakable, and this function cannot see which
    quoting its caller used.
    """
    out = str(text)
    for bad, good in _HTML:
        out = out.replace(bad, good)
    return Markup(out)


def raw(text: str) -> Markup:
    """Assert that `text` is already safe.

    Named for what it is so it stands out in a diff. Every call site should be
    a literal template written here, never anything derived from a repository.
    """
    return Markup(text)


EMPTY = Markup("")


def join(parts: Iterable[Markup], sep: str = "") -> Markup:
    """Concatenate already-escaped fragments.

    `Iterable[Markup]`, not `object`. The `object` version accepted
    `join([repo_string])` with no complaint, and the `# type: ignore` that
    silenced the resulting error was the signal that the signature was wrong.
    """
    return Markup(sep.join(parts))


def attrs(**pairs: object) -> Markup:
    """Render attributes, escaping every value.

    `object` is correct here, unlike on `tag` and `join`: this function escapes
    what it is given rather than trusting it, so a caller passing raw
    repository text is the intended use. The type is wide because the
    behaviour is safe, not in spite of it.

    `None` and `False` drop the attribute entirely rather than rendering the
    string "None", which would silently produce `class="None"`. Underscores in
    names become dashes, so `data_spec` writes `data-spec`.
    """
    out: list[str] = []
    for name, value in pairs.items():
        if value is None or value is False:
            continue
        key = name.rstrip("_").replace("_", "-")
        if value is True:
            out.append(f" {key}")
        else:
            out.append(f' {key}="{esc(value)}"')
    return Markup("".join(out))


def tag(name: str, body: Markup = EMPTY, /, **pairs: object) -> Markup:
    """One element.

    `body: Markup` is the whole point. It was `object`, which let
    `tag("text", node.label)` compile clean and inject.
    """
    if not body:
        return Markup(f"<{name}{attrs(**pairs)}></{name}>")
    return Markup(f"<{name}{attrs(**pairs)}>{body}</{name}>")
