"""Escaping, made structural rather than a matter of discipline.

Everything this stage writes into a document comes from a repository: module
names, file paths, labels. POSIX allows almost every byte in a filename, so a
directory named `</script><img src=x onerror=alert(1)>` is scannable, and the
artifact is opened in a browser by the person who ran the tool. Getting one
interpolation wrong is enough.

So the type system carries the invariant. `Markup` is the only thing a document
accepts, and the only ways to make one are `esc` (which escapes) and `raw`
(which announces in one word that it does not). Under pyright strict, dropping
a bare `str` where `Markup` is expected is a type error, which turns "someone
forgot to escape" from a review miss into a failed build.

A promoted decision says a docstring arguing a check is unnecessary is a check
that does not exist. This is the opposite construction: the guarantee is a
property of the types, and `tests/test_emit.py` still attacks it with real
adversarial names rather than trusting the argument.
"""

from __future__ import annotations

import json
from typing import NewType

__all__ = [
    "Markup",
    "attrs",
    "esc",
    "join",
    "json_script",
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


def join(parts: object, sep: str = "") -> Markup:
    """Concatenate already-escaped fragments."""
    return Markup(sep.join(str(p) for p in parts))  # type: ignore[union-attr]


def attrs(**pairs: object) -> Markup:
    """Render attributes, escaping every value.

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


def tag(name: str, body: object = "", /, **pairs: object) -> Markup:
    """One element. `body` must already be `Markup`."""
    inner = str(body)
    if not inner:
        return Markup(f"<{name}{attrs(**pairs)}></{name}>")
    return Markup(f"<{name}{attrs(**pairs)}>{inner}</{name}>")


def json_script(data: object, element_id: str) -> Markup:
    """Embed JSON in a `<script type="application/json">` block, safely.

    HTML escaping is wrong inside a script element: the browser does not decode
    entities there, so `&lt;` would arrive literally and break the parse.
    Instead the four characters that can end a script block or a JS string
    literal are written as `\\uXXXX`, which is valid JSON and cannot escape the
    element.

    `U+2028` and `U+2029` are included because they terminate a line in
    JavaScript source while being legal inside a JSON string, and both are
    legal in a POSIX filename. `json.dumps` leaves them raw by default.
    """
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Written as escapes, never as literals. U+2028 and U+2029 are invisible,
    # so a literal here would be unreviewable in a diff, which is exactly the
    # property that makes them worth escaping in the first place.
    for bad, good in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(bad, good)
    return Markup(f'<script type="application/json" id="{esc(element_id)}">{text}</script>')
