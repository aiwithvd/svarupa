"""JavaScript language pack: the TypeScript hooks, its own label, TSX grammar.

JavaScript files used to be parsed with the plain TypeScript grammar and
labelled `typescript`. The grammar choice broke JSX in `.js` and `.jsx`
files (the parse failed and facts went missing), and the label meant every
`javascript` branch downstream never ran.

TSX, not tree-sitter-javascript: the TypeScript hooks read TypeScript node
shapes (`class_heritage` with `extends_clause`) that the JavaScript grammar
spells differently, and the one construct TSX misreads, the `<T>x` type
assertion, does not exist in JavaScript.
"""

from __future__ import annotations

from dataclasses import replace

from svarupa.extract.packs import typescript
from svarupa.extract.packs.model import Maturity, Pack

__all__ = ["PACK"]

PACK: Pack = replace(
    typescript.PACK,
    lang="javascript",
    # One JavaScript repository in the benchmark so far; stable needs two.
    maturity=Maturity.EXPERIMENTAL,
    grammar=replace(typescript.PACK.grammar, default="language_tsx", by_suffix=()),
)
