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
