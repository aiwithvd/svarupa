"""TypeScript extraction tests.

Written with decoys from the first commit rather than retrofitted after a
review. Review #4's central lesson: a fixture with exactly one plausible target
cannot distinguish correct resolution from first-match-wins, and 33 Python
tests passed while five wrong-resolution bugs were live for exactly that reason.

The three traps this extractor was built to avoid were all measured in Spike 0c
on real repositories before any of it was written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.base import CallShape, node_id
from svarupa.extract.typescript import TypeScriptExtractor
from svarupa.model import EdgeKind, Resolution
from svarupa.tsconfig import load_aliases, strip_jsonc


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


def facts(src: str, path: str = "src/mod.ts"):
    return TypeScriptExtractor().parse(path, src.encode())


# --------------------------------------------------------------------------
# Pass 1
# --------------------------------------------------------------------------


def test_collects_classes_methods_and_functions() -> None:
    f = facts(
        "export class Svc {\n  find(): void {}\n}\n\n"
        "export function helper(): void {}\n\n"
        "export const arrow = () => {};\n"
    )
    kinds = {s.name: s.kind for s in f.symbols}
    assert kinds == {
        "Svc": "class",
        "find": "method",
        "helper": "function",
        "arrow": "function",
    }


def test_interfaces_are_captured() -> None:
    f = facts("export interface Repo {\n  get(): void;\n}\n")
    assert [s.kind for s in f.symbols] == ["interface"]


@pytest.mark.parametrize(
    ("src", "shape", "receiver"),
    [
        ("function f() { helper(); }", CallShape.BARE, None),
        ("class A { m() { this.other(); } }", CallShape.SELF, "this"),
        ("class A { m() { super.m(); } }", CallShape.SUPER, "super"),
        ("class A { m() { this.svc.find(); } }", CallShape.SELF_FIELD, "svc"),
        ("function f() { mod.thing(); }", CallShape.QUALIFIED, "mod"),
        ("function f() { a.b.thing(); }", CallShape.MEMBER, "a.b"),
    ],
)
def test_call_shapes(src: str, shape: CallShape, receiver: str | None) -> None:
    calls = list(facts(src).calls)
    assert calls, "expected a call site"
    assert calls[0].shape is shape
    assert calls[0].receiver == receiver


def test_globals_are_not_call_sites() -> None:
    assert facts("function f() { console.log(JSON.stringify({})); }").calls == ()


def test_constructor_injection_records_field_types() -> None:
    """The highest-yield fact in TypeScript extraction.

    It is what makes `this.svc.method()` resolvable, and it pinned at 99-100%
    on DI-heavy code where Python's nearest equivalent pinned at nearly nothing.
    """
    f = facts(
        "class Ctrl {\n"
        "  constructor(private readonly svc: UserService, public repo: Repo) {}\n"
        "}\n"
    )
    got = {(x.owner, x.field, x.type_name) for x in f.fields}
    assert got == {("Ctrl", "svc", "UserService"), ("Ctrl", "repo", "Repo")}


def test_generic_annotation_keeps_the_outer_type() -> None:
    """`Repository<User>` means the field IS a Repository.

    `this.repo.find()` dispatches to `Repository.find`, not to anything on
    `User`, so the outer constructor is the right answer. The nullable union is
    stripped because `UserService | null` is still a UserService.
    """
    f = facts(
        "class C {\n"
        "  constructor(private repo: Repository<User>, private svc: UserService | null) {}\n"
        "}\n"
    )
    got = {x.field: x.type_name for x in f.fields}
    assert got == {"repo": "Repository", "svc": "UserService"}


def test_barrel_reexport_is_recorded() -> None:
    f = facts("export { Thing } from './core';\n", "src/index.ts")
    assert f.imports[0].is_reexport
    assert f.imports[0].names == ("Thing",)


def test_aliased_import_keeps_the_local_name() -> None:
    f = facts("import { Real as Alias } from './x';\n")
    assert f.imports[0].alias_of == {"Alias": "Real"}
    assert "Alias" in f.imports[0].names


def test_tsx_parses() -> None:
    f = facts("export const C = () => <div onClick={handler} />;\n", "src/c.tsx")
    assert any(s.name == "C" for s in f.symbols)


# --------------------------------------------------------------------------
# Spike 0c trap 1: ESM `.js` specifiers naming `.ts` files
# --------------------------------------------------------------------------


def test_esm_js_specifier_resolves_to_the_ts_file(tmp_path: Path) -> None:
    """Modern ESM TypeScript writes `./foo.js` for a file that is `foo.ts`.

    Without this remap, zod scored **0.0%** import resolution. With it, 96.3%.
    """
    write(tmp_path, "src/core.ts", "export function go(): void {}\n")
    write(tmp_path, "src/api.ts", "import { go } from './core.js';\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("src/api.ts", "src/core.ts") in imports


def test_extensionless_and_index_specifiers_resolve(tmp_path: Path) -> None:
    write(tmp_path, "src/lib/index.ts", "export const x = 1;\n")
    write(tmp_path, "src/plain.ts", "export const y = 2;\n")
    write(tmp_path, "src/app.ts", "import { x } from './lib';\nimport { y } from './plain';\n")
    res = run(tmp_path)
    targets = {e.dst for e in res.edges if e.kind is EdgeKind.IMPORTS and e.src == "src/app.ts"}
    assert targets == {"src/lib/index.ts", "src/plain.ts"}


# --------------------------------------------------------------------------
# Spike 0c trap 2 and 3: tsconfig
# --------------------------------------------------------------------------


def test_jsonc_stripper_preserves_glob_patterns() -> None:
    """A regex cannot do this, and the failure is silent.

    `"@/*": ["src/*"]` contains `/*`, which a block-comment regex treats as a
    comment opener and deletes through the next `*/`, mangling the file into
    invalid JSON. One project scored 61.3% import resolution instead of 92.7%.
    """
    raw = '{\n  // a comment\n  "paths": {\n    "@/*": ["src/*"],\n    "@x/*": ["y/*"]\n  }\n}'
    out = strip_jsonc(raw)
    assert '"@/*"' in out and '"src/*"' in out
    assert "a comment" not in out
    import json

    assert json.loads(out)["paths"]["@/*"] == ["src/*"]


def test_tsconfig_aliases_resolve(tmp_path: Path) -> None:
    write(
        tmp_path,
        "tsconfig.json",
        '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
    )
    write(tmp_path, "src/store/config.ts", "export const cfg = 1;\n")
    write(tmp_path, "src/app.ts", "import { cfg } from '@/store/config';\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("src/app.ts", "src/store/config.ts") in imports


def test_aliases_are_found_through_project_references(tmp_path: Path) -> None:
    """The project-references layout puts `"files": []` in the root config.

    Reading only the root finds no aliases at all.
    """
    write(
        tmp_path, "tsconfig.json", '{"files":[],"references":[{"path":"./tsconfig.web.json"}]}'
    )
    write(
        tmp_path,
        "tsconfig.web.json",
        '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/renderer/*"]}}}',
    )
    assert ("@", "src/renderer") in load_aliases(tmp_path)


def test_aliases_are_found_through_extends(tmp_path: Path) -> None:
    write(tmp_path, "tsconfig.base.json", '{"compilerOptions":{"paths":{"~/*":["lib/*"]}}}')
    write(tmp_path, "tsconfig.json", '{"extends":"./tsconfig.base.json"}')
    assert ("~", "lib") in load_aliases(tmp_path)


def test_longest_alias_wins(tmp_path: Path) -> None:
    """`@/store/x` must prefer `@/store` over the shorter `@`."""
    write(
        tmp_path,
        "tsconfig.json",
        '{"compilerOptions":{"paths":{"@/*":["src/*"],"@/store/*":["state/*"]}}}',
    )
    write(tmp_path, "state/thing.ts", "export const a = 1;\n")
    write(tmp_path, "src/store/thing.ts", "export const b = 2;\n")
    write(tmp_path, "src/app.ts", "import { a } from '@/store/thing';\n")
    res = run(tmp_path)
    targets = {e.dst for e in res.edges if e.kind is EdgeKind.IMPORTS and e.src == "src/app.ts"}
    assert targets == {"state/thing.ts"}


# --------------------------------------------------------------------------
# Decoys: catching a WRONG resolution
# --------------------------------------------------------------------------


def test_relative_import_does_not_match_a_same_named_file_elsewhere(tmp_path: Path) -> None:
    write(tmp_path, "src/a/utils.ts", "export function real(): void {}\n")
    write(tmp_path, "src/b/utils.ts", "export function decoy(): void {}\n")
    write(tmp_path, "src/a/main.ts", "import { real } from './utils';\n")
    res = run(tmp_path)
    targets = {
        e.dst for e in res.edges if e.kind is EdgeKind.IMPORTS and e.src == "src/a/main.ts"
    }
    assert targets == {"src/a/utils.ts"}


def test_self_field_binds_the_declared_type_not_a_same_named_method(tmp_path: Path) -> None:
    """`this.svc.find()` must follow the constructor annotation.

    Two classes define `find`; only the annotated one is correct.
    """
    write(tmp_path, "src/real.ts", "export class UserService {\n  find(): void {}\n}\n")
    write(tmp_path, "src/decoy.ts", "export class OtherService {\n  find(): void {}\n}\n")
    write(
        tmp_path,
        "src/ctrl.ts",
        "import { UserService } from './real';\n"
        "export class Ctrl {\n"
        "  constructor(private readonly svc: UserService) {}\n"
        "  go(): void { this.svc.find(); }\n"
        "}\n",
    )
    res = run(tmp_path)
    calls = [e for e in res.edges if e.kind is EdgeKind.CALLS]
    assert calls, "constructor-injected call did not resolve"
    assert calls[0].dst == node_id("src/real.ts", "src.real.UserService.find")


def test_barrel_reexport_resolves_to_the_definition(tmp_path: Path) -> None:
    """The TS analogue of a package `__init__`.

    Stopping at the barrel reports `index.ts` as the dependency target for most
    of a library's public API.
    """
    write(tmp_path, "src/core.ts", "export class Thing {\n  go(): void {}\n}\n")
    write(tmp_path, "src/index.ts", "export { Thing } from './core';\n")
    write(tmp_path, "app.ts", "import { Thing } from './src';\n")
    res = run(tmp_path)
    refs = [e for e in res.edges if e.kind is EdgeKind.REFERENCES and e.src == "app.ts"]
    assert refs, "expected a symbol reference through the barrel"
    assert refs[0].dst == node_id("src/core.ts", "src.core.Thing")


def test_inheritance_across_files(tmp_path: Path) -> None:
    write(tmp_path, "src/base.ts", "export class Base {\n  m(): void {}\n}\n")
    write(tmp_path, "src/decoy.ts", "export class Base {\n  m(): void {}\n}\n")
    write(
        tmp_path,
        "src/child.ts",
        "import { Base } from './base';\nexport class Child extends Base {}\n",
    )
    res = run(tmp_path)
    inh = [e for e in res.edges if e.kind is EdgeKind.INHERITS]
    assert inh, "no inheritance edge"
    assert "decoy" not in inh[0].dst, f"bound to the decoy: {inh[0].dst}"


# --------------------------------------------------------------------------
# Scorecard honesty
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    ["typeorm/common/DeepPartial", "@nestjs/common/exceptions/http.exception", "node:fs", "fs"],
)
def test_subpath_and_scoped_specifiers_are_external(tmp_path: Path, spec: str) -> None:
    """`top` was derived with Python's rule, splitting on ".".

    `typeorm/common/DeepPartial` then matched no declared dependency, so an
    ordinary framework import landed in the unresolved bin and the scorecard
    cried wolf.
    """
    write(
        tmp_path,
        "package.json",
        '{"name":"x","dependencies":{"typeorm":"1","@nestjs/common":"1"}}',
    )
    write(tmp_path, "src/a.ts", f"import {{ X }} from '{spec}';\n")
    res = run(tmp_path)
    assert res.scorecard.get("typescript", "imports", Resolution.EXTERNAL) == 1
    assert res.scorecard.get("typescript", "imports", Resolution.UNRESOLVED) == 0


def test_undeclared_package_is_unresolved_not_external(tmp_path: Path) -> None:
    write(tmp_path, "package.json", '{"name":"x","dependencies":{}}')
    write(tmp_path, "src/a.ts", "import { X } from 'never-heard-of-it';\n")
    res = run(tmp_path)
    assert res.scorecard.get("typescript", "imports", Resolution.UNRESOLVED) == 1
    assert res.scorecard.get("typescript", "imports", Resolution.EXTERNAL) == 0


def test_workspace_package_name_resolves_intra_repo(tmp_path: Path) -> None:
    """A monorepo publishes `packages/zod` as `zod`.

    `zod/v4` is then an intra-repo import that no tsconfig alias covers, and it
    landed in the unresolved bin despite being right there in the tree.
    """
    write(tmp_path, "package.json", '{"name":"root","workspaces":["packages/*"]}')
    write(tmp_path, "packages/lib/package.json", '{"name":"mylib"}')
    write(tmp_path, "packages/lib/src/v2.ts", "export const x = 1;\n")
    write(tmp_path, "apps/web/package.json", '{"name":"web"}')
    write(tmp_path, "apps/web/app.ts", "import { x } from 'mylib/v2';\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("apps/web/app.ts", "packages/lib/src/v2.ts") in imports


def test_asset_imports_are_external_not_a_failure(tmp_path: Path) -> None:
    """Importing a PNG is a real dependency, just not a code one."""
    write(tmp_path, "package.json", '{"name":"x"}')
    write(tmp_path, "src/a.ts", "import logo from './logo.png';\nimport './style.css';\n")
    res = run(tmp_path)
    assert res.scorecard.get("typescript", "imports", Resolution.UNRESOLVED) == 0
    assert res.scorecard.get("typescript", "imports", Resolution.EXTERNAL) == 2


def test_javascript_files_use_the_typescript_grammar(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", "export function go() {}\n")
    write(tmp_path, "src/b.js", "import { go } from './a.js';\n")
    res = run(tmp_path)
    imports = {(e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS}
    assert ("src/b.js", "src/a.js") in imports


def test_grammar_version_matches_the_installed_pin() -> None:
    from importlib.metadata import version

    assert TypeScriptExtractor.grammar_version == version("tree-sitter-typescript")
