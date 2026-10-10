"""What a language pack is made of.

A pack maps syntax node types to rules. Rules are data: which field holds
the name, which holds the body, whether the definition opens a class scope.
Hooks are the few functions a language needs because data cannot say it:
how an import is spelled, which call target is a builtin, how a string
literal decodes. The walker reads the rules; it never knows the language.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from tree_sitter import Node as TSNode

from svarupa.extract.base import CallSite, DecoratorRef, FieldType, ImportRef

if TYPE_CHECKING:
    from svarupa.extract.packs.modules import ModuleContext, ModuleResolver
    from svarupa.extract.packs.walker import Ctx, Frame

__all__ = [
    "AfterHook",
    "BasesHook",
    "Call",
    "CallHook",
    "Carry",
    "Custom",
    "CustomHook",
    "Decorated",
    "DecoratorHook",
    "DecoratorsHook",
    "Define",
    "ExportedHook",
    "Field",
    "FieldHook",
    "Grammar",
    "Import",
    "ImportHook",
    "Maturity",
    "MetricsSpec",
    "Pack",
    "ParamsHook",
    "Rule",
]

# Ctx and Frame are quoted: walker.py imports this module, so they exist
# only for the type checker here.
ImportHook = Callable[["Ctx", TSNode], ImportRef | None]
CallHook = Callable[["Ctx", TSNode, "Frame"], CallSite | None]
FieldHook = Callable[["Ctx", TSNode, str], list[FieldType]]
CustomHook = Callable[["Ctx", TSNode, "Frame", int], None]
DecoratorHook = Callable[["Ctx", TSNode], DecoratorRef | None]
BasesHook = Callable[["Ctx", TSNode], tuple[str, ...]]
AfterHook = Callable[["Ctx", TSNode, "Frame", str], None]
ParamsHook = Callable[[TSNode], int]
ExportedHook = Callable[["Ctx", TSNode, str], bool]
DecoratorsHook = Callable[["Ctx", TSNode], tuple[DecoratorRef, ...]]


class Maturity(str, Enum):
    """Stable once the benchmark proves a pack; experimental until then."""

    STABLE = "stable"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True, slots=True)
class Define:
    """A definition: emits one SymbolRef, then walks only its body.

    Walking only the body is the contract, not an optimization: calls in
    default arguments, base lists and decorator arguments have never been
    recorded, and the golden facts pin that.
    """

    kind: str
    kind_in_class: str | None = None  # Python: a def under a class is a method
    name_field: str = "name"
    body_field: str | None = "body"  # None: the body is not walked
    inherit_exported: bool = False  # False: exported means no leading underscore
    sets_class: bool = False  # the body's frame gets this name as its class
    records_class: bool = True  # fill SymbolRef.enclosing_class
    keeps_decorators: bool = True  # take pending decorators from the frame
    recurse_without_name: bool = False  # nameless node: walk children, or stop
    own_decorators: str | None = None  # child type read as own decorators
    member_decorators: str | None = None  # body child type paired with next member
    bases: BasesHook | None = None
    after: AfterHook | None = None  # runs after the symbol, before the body
    exported_by: ExportedHook | None = None  # Java `public`, Go capital letter
    decorators_from: DecoratorsHook | None = None  # Java annotations in `modifiers`


@dataclass(frozen=True, slots=True)
class Decorated:
    """Decorators wrapping one definition; only the definition is walked."""

    decorator_type: str = "decorator"
    definition_field: str = "definition"


@dataclass(frozen=True, slots=True)
class Import:
    hook: ImportHook
    recurse_on_none: bool = False  # walk children when the hook finds no import


@dataclass(frozen=True, slots=True)
class Call:
    """A call. Its children are walked too: arguments hold calls."""

    hook: CallHook


@dataclass(frozen=True, slots=True)
class Field:
    """A typed class field. Only inside a class; children are walked too."""

    hook: FieldHook


@dataclass(frozen=True, slots=True)
class Carry:
    """Walk the children and keep the exported flag (`export const f = ...`)."""


@dataclass(frozen=True, slots=True)
class Custom:
    """The hook owns this node, including whether and how to walk below it."""

    hook: CustomHook


Rule = Define | Decorated | Import | Call | Field | Carry | Custom


@dataclass(frozen=True, slots=True)
class Grammar:
    distribution: str  # PyPI name, pinned == in pyproject.toml
    version: str  # must equal the installed distribution's version
    module: str  # import name
    default: str  # function in `module` returning the language pointer
    by_suffix: tuple[tuple[str, str], ...] = ()  # (".tsx", "language_tsx")


@dataclass(frozen=True, slots=True)
class MetricsSpec:
    """Which syntax nodes are functions, decisions and nesting blocks."""

    function_types: frozenset[str]
    branch_types: frozenset[str]
    nesting_types: frozenset[str]
    count_params: ParamsHook
    # Node type -> operator tokens that make it a decision (`a && b`).
    boolean_ops: tuple[tuple[str, frozenset[str]], ...] = ()
    # (node type, child token) pairs that are not a decision (`default:`).
    not_branch: tuple[tuple[str, str], ...] = ()
    # An `if` directly under one of these is an `else if`: no extra nesting.
    else_if_parents: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Pack:
    lang: str  # the label every fact from this pack carries
    grammar: Grammar
    maturity: Maturity
    rules: Mapping[str, Rule]
    qualified_prefix: Callable[[str], str]
    decorator: DecoratorHook | None = None
    # Imports that name packages need their own resolver; None means the
    # language resolves in resolve.py (Python, TypeScript, JavaScript).
    modules: Callable[[ModuleContext], ModuleResolver] | None = None
    metrics: MetricsSpec | None = None
