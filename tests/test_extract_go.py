"""Go extraction: packages, receivers and go.mod modules."""

from __future__ import annotations

from pathlib import Path

from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.extract.packs import extractor
from svarupa.model import EdgeKind, Resolution


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def run(root: Path):
    scan = detect(root)
    return extract(scan, declared_dependencies(scan))


GO_MOD = (
    "module github.com/acme/shop\n\ngo 1.22\n\nrequire (\n"
    "\tgithub.com/gin-gonic/gin v1.9.1 // web\n)\n"
)


def shop(root: Path) -> None:
    write(root, "go.mod", GO_MOD)
    write(
        root,
        "cmd/api/main.go",
        'package main\n\nimport (\n\t"fmt"\n\t"github.com/gin-gonic/gin"\n'
        '\t"github.com/acme/shop/internal/orders"\n\t"github.com/acme/unknown/x"\n)\n\n'
        "func main() {\n\tr := gin.Default()\n\tsvc := orders.NewService()\n"
        "\tfmt.Println(svc, r, x.Y())\n}\n",
    )
    write(
        root,
        "internal/orders/service.go",
        "package orders\n\ntype Service struct{ repo *Repo }\n\n"
        "func NewService() *Service {\n\treturn &Service{repo: newRepo()}\n}\n\n"
        "func (s *Service) Place() error {\n\treturn s.repo.Save()\n}\n",
    )
    write(
        root,
        "internal/orders/repo.go",
        "package orders\n\ntype Repo struct{}\n\nfunc newRepo() *Repo { return &Repo{} }\n\n"
        "func (r *Repo) Save() error { return nil }\n",
    )
    write(root, "internal/orders/service_test.go", "package orders\n\nfunc TestPlace() {}\n")


def test_a_package_import_points_at_its_source_files(tmp_path: Path) -> None:
    shop(tmp_path)
    res = run(tmp_path)
    imports = sorted((e.src, e.dst) for e in res.edges if e.kind is EdgeKind.IMPORTS)
    assert imports == [
        ("cmd/api/main.go", "internal/orders/repo.go"),
        ("cmd/api/main.go", "internal/orders/service.go"),
    ]


def test_go_imports_land_in_the_right_bins(tmp_path: Path) -> None:
    shop(tmp_path)
    card = run(tmp_path).scorecard
    assert card.get("go", "imports", Resolution.RESOLVED) == 1
    assert card.get("go", "imports", Resolution.EXTERNAL) == 2  # fmt, gin
    assert card.get("go", "imports", Resolution.UNRESOLVED) == 1  # acme/unknown/x


def test_a_qualified_call_resolves_into_another_file_of_the_package(tmp_path: Path) -> None:
    shop(tmp_path)
    calls = {(e.src, e.dst) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert (
        "cmd/api/main.go#cmd.api.main.main",
        "internal/orders/service.go#internal.orders.service.NewService",
    ) in calls
    assert (
        "internal/orders/service.go#internal.orders.service.NewService",
        "internal/orders/repo.go#internal.orders.repo.newRepo",
    ) in calls


def test_methods_attach_to_their_receiver_type() -> None:
    f = extractor("go").parse(
        "svc/s.go",
        b"package svc\n\ntype S struct{}\n\nfunc (s *S) Run() {}\n\nfunc (g G[T]) Go() {}\n",
    )
    got = [(s.qualified_name, s.kind, s.enclosing_class, s.exported) for s in f.symbols]
    assert got == [
        ("svc.s.S", "class", None, True),
        ("svc.s.S.Run", "method", "S", True),
        ("svc.s.G.Go", "method", "G", True),
    ]


def test_exported_means_a_capital_first_letter() -> None:
    f = extractor("go").parse("a.go", b"package a\n\nfunc Up() {}\n\nfunc down() {}\n")
    assert [(s.name, s.exported) for s in f.symbols] == [("Up", True), ("down", False)]


def test_go_mod_requires_are_declared_dependencies(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", GO_MOD + "require golang.org/x/sync v0.7.0\n")
    deps = declared_dependencies(detect(tmp_path))
    assert {"github.com/gin-gonic/gin", "golang.org/x/sync"} <= deps


def test_a_package_call_never_lands_on_a_method(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", "module github.com/acme/shop\n")
    write(
        tmp_path,
        "cmd/main.go",
        'package main\n\nimport "github.com/acme/shop/orders"\n\nfunc main() { orders.Validate() }\n',
    )
    write(
        tmp_path,
        "orders/a.go",
        "package orders\n\ntype S struct{}\n\nfunc (s *S) Validate() {}\n",
    )
    calls = [(e.dst, e.resolution) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS]
    assert not [c for c in calls if ".S.Validate" in c[0]], calls


def test_a_bare_call_resolves_only_inside_its_own_package(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", "module github.com/acme/shop\n")
    write(tmp_path, "articles/routers.go", "package articles\n\nfunc Create() { SaveOne() }\n")
    write(tmp_path, "articles/models.go", "package articles\n\nfunc SaveOne() {}\n")
    write(tmp_path, "users/models.go", "package users\n\nfunc SaveOne() {}\n")
    calls = [(e.dst, e.resolution) for e in run(tmp_path).edges if e.kind is EdgeKind.CALLS]
    assert calls == [("articles/models.go#articles.models.SaveOne", Resolution.RESOLVED)]


# --------------------------------------------------------------------------
# Parameter and local types
# --------------------------------------------------------------------------

REPO_GO = "package orders\n\ntype Repo struct{}\n\nfunc (r *Repo) Save() error { return nil }\n"
OTHER_GO = "package orders\n\ntype Other struct{}\n\nfunc (o *Other) Save() error { return nil }\nfunc (o *Other) X() {}\n"


def locals_of(src: str) -> set[tuple[str, str, str]]:
    f = extractor("go").parse("svc/a.go", ("package svc\n\n" + src).encode())
    return {(x.function, x.name, x.type_name) for x in f.locals}


def typed_calls(tmp_path: Path, body: str) -> set[str]:
    write(tmp_path, "go.mod", GO_MOD)
    write(tmp_path, "internal/orders/repo.go", REPO_GO)
    write(tmp_path, "internal/orders/other.go", OTHER_GO)
    write(tmp_path, "internal/orders/a.go", "package orders\n\n" + body)
    res = run(tmp_path)
    return {e.dst for e in res.edges if e.kind is EdgeKind.CALLS}


def saves_repo(calls: set[str]) -> bool:
    return any(d.endswith("Repo.Save") for d in calls)


def test_a_typed_parameter_resolves(tmp_path: Path) -> None:
    assert saves_repo(typed_calls(tmp_path, "func F(r *Repo) { r.Save() }\n"))


def test_a_var_declaration_resolves(tmp_path: Path) -> None:
    assert saves_repo(typed_calls(tmp_path, "func F() { var r Repo\n r.Save() }\n"))


def test_a_short_declaration_is_not_inferred(tmp_path: Path) -> None:
    body = "func F() { r := NewRepo()\n r.Save() }\nfunc NewRepo() *Repo { return nil }\n"
    assert locals_of("func F() { r := NewRepo() }") == {("svc.a.F", "r", "")}
    assert not saves_repo(typed_calls(tmp_path, body))


def test_shared_types_pointers_variadics_and_results() -> None:
    got = locals_of(
        "func F(a, b *Repo, c ...Repo, d **Repo, e []Repo) (res Repo, err error) {}"
    )
    assert got == {
        ("svc.a.F", "a", "Repo"),
        ("svc.a.F", "b", "Repo"),
        ("svc.a.F", "c", ""),
        ("svc.a.F", "d", ""),
        ("svc.a.F", "e", ""),
        ("svc.a.F", "res", "Repo"),
        ("svc.a.F", "err", ""),
    }


def test_generic_parameters_are_skipped() -> None:
    got = locals_of(
        "func F[T any, U Repo](a T, b U, c Repo) {}\nfunc (s *Stack[V]) M(v V, c *Repo) {}\n"
    )
    assert got == {
        ("svc.a.F", "a", ""),
        ("svc.a.F", "b", ""),
        ("svc.a.F", "c", "Repo"),
        ("svc.a.Stack.M", "v", ""),
        ("svc.a.Stack.M", "c", "Repo"),
    }


def test_the_receiver_is_not_a_local() -> None:
    assert locals_of("func (s *Svc) M(r Repo) {}") == {("svc.a.Svc.M", "r", "Repo")}


def test_every_binding_form_is_recorded() -> None:
    got = locals_of(
        "func F(ch chan int) {\n"
        "  var a, b Repo\n  var c = 1\n  const k Color = 1\n"
        "  d, e := f()\n"
        "  for i, v := range xs {}\n"
        "  for j = range xs {}\n"
        "  if x, ok := g(); ok {}\n"
        "  select { case m, ok2 := <-ch: }\n"
        "  h := func(p Repo) { q := 1 }\n"
        "}\n"
    )
    assert got == {
        ("svc.a.F", "ch", ""),
        ("svc.a.F", "a", "Repo"),
        ("svc.a.F", "b", "Repo"),
        ("svc.a.F", "c", ""),
        ("svc.a.F", "k", "Color"),
        ("svc.a.F", "d", ""),
        ("svc.a.F", "e", ""),
        ("svc.a.F", "i", ""),
        ("svc.a.F", "v", ""),
        ("svc.a.F", "x", ""),
        ("svc.a.F", "ok", ""),
        ("svc.a.F", "m", ""),
        ("svc.a.F", "ok2", ""),
        ("svc.a.F", "h", ""),
        ("svc.a.F", "p", "Repo"),
        ("svc.a.F", "q", ""),
    }


def test_a_type_assertion_binds_the_asserted_type() -> None:
    got = locals_of(
        "func F(x any) { y := x.(*Repo)\n z, ok := x.(Other)\n w := x.(interface{ M() }) }"
    )
    assert got == {
        ("svc.a.F", "x", ""),
        ("svc.a.F", "y", "Repo"),
        ("svc.a.F", "z", "Other"),
        ("svc.a.F", "ok", ""),
        ("svc.a.F", "w", ""),
    }


def test_a_type_assertion_resolves(tmp_path: Path) -> None:
    assert saves_repo(typed_calls(tmp_path, "func F(x any) { y := x.(*Repo)\n y.Save() }\n"))


def test_a_type_switch_with_two_types_stays_untyped(tmp_path: Path) -> None:
    src = "func F(x any) { switch v := x.(type) { case *Repo, *Other: v.Save()\n default: } }\n"
    assert ("svc.a.F", "v", "") in locals_of(src)
    assert not typed_calls(tmp_path, src) & {"internal.orders.Repo.Save"}


def test_a_type_switch_clause_with_one_type_is_typed() -> None:
    got = locals_of("func F(x any) { switch v := x.(type) { case *Repo: v.Save() } }")
    assert ("svc.a.F", "v", "Repo") in got


def test_clauses_with_different_types_merge_to_untyped(tmp_path: Path) -> None:
    src = (
        "func F(x any) {\n switch v := x.(type) {\n case *Repo: v.Save()\n"
        " case *Other: v.Save()\n }\n}\n"
    )
    assert not any(d.endswith(".Save") for d in typed_calls(tmp_path, src))


def test_a_shadowing_local_hides_a_typed_parameter(tmp_path: Path) -> None:
    src = "func F(r *Repo) { r := other()\n r.Save() }\nfunc other() int { return 0 }\n"
    assert not saves_repo(typed_calls(tmp_path, src))


def test_a_closure_sees_the_outer_parameter(tmp_path: Path) -> None:
    src = "func F(r *Repo) { go func() { r.Save() }() }\n"
    assert saves_repo(typed_calls(tmp_path, src))


def test_a_closure_parameter_resolves(tmp_path: Path) -> None:
    assert saves_repo(
        typed_calls(tmp_path, "func F() { f := func(r *Repo) { r.Save() }\n f(nil) }\n")
    )


def test_a_closure_local_hides_the_outer_one(tmp_path: Path) -> None:
    src = "func F(r *Repo) { go func() { r := other()\n r.Save() }() }\nfunc other() int { return 0 }\n"
    assert not saves_repo(typed_calls(tmp_path, src))


def test_an_interface_parameter_never_picks_an_implementation(tmp_path: Path) -> None:
    # The pack does not record interface methods as symbols, so there is no
    # method to land on; the call must stay unresolved, not guess `Repo.Save`.
    got = typed_calls(
        tmp_path,
        "type Saver interface { Save() error }\nfunc F(s Saver) { s.Save() }\n",
    )
    assert not any(d.endswith(".Save") for d in got)


def test_a_parameter_from_another_package_resolves(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", GO_MOD)
    write(tmp_path, "internal/orders/repo.go", REPO_GO)
    write(
        tmp_path,
        "cmd/api/main.go",
        'package main\n\nimport "github.com/acme/shop/internal/orders"\n\n'
        "func F(r *orders.Repo) { r.Save() }\n",
    )
    res = run(tmp_path)
    assert any(e.kind is EdgeKind.CALLS and e.dst.endswith("Repo.Save") for e in res.edges)


def test_a_qualified_type_never_matches_a_repo_type_of_the_same_name(tmp_path: Path) -> None:
    write(tmp_path, "go.mod", GO_MOD)
    write(tmp_path, "internal/orders/repo.go", REPO_GO)
    write(
        tmp_path,
        "cmd/api/main.go",
        'package main\n\nimport "github.com/gin-gonic/gin"\n\n'
        "type Repo struct{}\nfunc (r *Repo) Save() {}\n"
        "func F(r *gin.Repo) { r.Save() }\n",
    )
    res = run(tmp_path)
    assert not any(e.kind is EdgeKind.CALLS and e.dst.endswith("Save") for e in res.edges)
