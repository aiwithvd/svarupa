# Plain Explanations (1c-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every health check gets a fixed plain explanation (what, why, how to fix, a before/after in the finding's language), shown in every output; every diagram box gets one plain sentence that says what it is and how healthy it is.

**Architecture:** Explanations are TOML files in `svarupa/health/explain/`, loaded by `svarupa/health/explain.py` and attached to `Health.to_json()` once per check. REPORT.md, the viewer and a new `svarupa explain` command render them. Box lines come from `svarupa/derive/about.py`, computed once per run from the graph, the health result and the drawn diagrams, then written to `graph.json["about"]`, `get_node` and the passport.

**Tech Stack:** Python 3.10+, `tomllib`/`tomli`, `importlib.resources`, hatchling wheel (`packages = ["svarupa"]` already ships non-Python files under the package), jsdom harness for the viewer script.

**Spec:** `docs/superpowers/specs/2026-10-24-plain-explanations-1c4-design.md`

## Global Constraints

- No AI in the pipeline: all text is fixed in the repository.
- Plain, simple English; no em dashes anywhere (text files, code, docs).
- Example languages: `python`, `typescript`, `go`, `java`; `javascript` findings use the `typescript` example.
- A check needs an example for every language whose pack can produce it; the required set is computed from pack data.
- The text is stored once per check in graph.json (`health.checks[].explanation`), never copied onto violations.
- graph.json stays byte-deterministic (`sort_keys`); SVG boxes do not change.
- The viewer script contains no `<` character (the emit suite checks escaped markup) and every backslash in it is doubled (it lives in a Python string).
- Commits: SSH-signed (repo-local config), imperative subject under 72 characters, no AI attribution or co-author lines.

## Rulings on the spec

- **Table line dropped.** No extractor produces `NodeKind.TABLE` nodes today (the ERD reports itself unavailable), so a table template cannot be tested; a table box would get the generic line. Cost if wrong: one template to add when tables are extracted.
- **Endpoint line is per module.** Diagrams draw one ingress box per module (`in:<module>`, `req:<module>`), not one per route, so the endpoint line reads "HTTP entry to app/api: 3 routes, for example GET /orders, POST /orders." Cost if wrong: none; there is no per-route box to describe.
- **Viewer fold is per check, not per violation.** One `<details id="why-<check>">` per check in the Health tab, and each violation gets a "why?" link to it. A fold per violation would copy the same text hundreds of times on a large repo. Cost if wrong: one click instead of zero.

## Review Focus

1. A JavaScript finding shows the TypeScript example, never nothing. Pinned in Task 1 (`test_javascript_uses_the_typescript_example`) and Task 4 (report fence for a `.js` violation).
2. A box with no facts reads its kind and id only; no clause is invented. Pinned in Task 5 (`test_a_module_with_no_facts_reads_its_id`).
3. The repository root module (`""` inside, `.` on the map) gets its own findings. Pinned in Task 5 (`test_root_module_box_counts_root_findings`).
4. Repository text (a service name with `</script>`) cannot break out of the embedded viewer data. Pinned in Task 6 (`test_about_data_is_escaped_in_the_page`).
5. `svarupa explain` with an unknown id or a language with no example fails or degrades clearly. Pinned in Task 3.

---

## File Structure

- Create `svarupa/health/explain.py`: `Example`, `Explanation`, `explanation`, `example_for`, `required_languages`, `ExplanationError`.
- Create `svarupa/health/explain/<check-id>.toml` (30 files).
- Modify `svarupa/health/model.py`: `Health.to_json` adds `explanation` per check.
- Modify `svarupa/cli.py`: `svarupa explain`.
- Create `svarupa/emit/explained.py`: `file_lang(graph, path)`, `explanation_markdown(check_id, lang)`.
- Modify `svarupa/emit/report.py`, `svarupa/emit/viewer.py`.
- Create `svarupa/derive/about.py`: `About`, `about_lines(graph, health, produced)`.
- Modify `svarupa/emit/data.py`, `svarupa/emit/__init__.py`, `svarupa/query/__init__.py`.
- Tests: `tests/test_explain.py`, `tests/test_about.py`, `tests/test_health_outputs.py`, `tests/js/viewer_harness.js`.
- `scripts/mutate_packs.py`, `docs/health.md`.

---

### Task 1: The explanation loader

**Files:**
- Create: `svarupa/health/explain.py`
- Create: `svarupa/health/explain/query-in-loop.toml`
- Test: `tests/test_explain.py`

**Interfaces:**
- Produces:
  - `Example(before: str, after: str)`, frozen dataclass.
  - `Explanation(check: str, what: str, why: str, fix: str, examples: tuple[tuple[str, Example], ...])`, frozen dataclass, with `to_json() -> dict[str, object]` returning `{"what", "why", "fix", "examples": {lang: {"before", "after"}}}`.
  - `explanation(check_id: str) -> Explanation` (cached; raises `ExplanationError` on a missing file, unknown key, empty field or unknown language).
  - `example_for(check_id: str, lang: str | None) -> Example | None` (`javascript` maps to `typescript`).
  - `required_languages(check_id: str) -> frozenset[str]`.
  - `LANGS = ("python", "typescript", "go", "java")`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_explain.py`)

```python
"""Check explanations: fixed text per check, examples per language."""

from __future__ import annotations

import pytest

from svarupa.health import explain
from svarupa.health.explain import (
    Example,
    ExplanationError,
    example_for,
    explanation,
    parse,
    required_languages,
)


def test_an_explanation_has_text_and_examples() -> None:
    e = explanation("query-in-loop")
    assert e.what.startswith("The same database query runs once for every item")
    assert e.why and e.fix
    langs = [lang for lang, _ in e.examples]
    assert langs == sorted(langs) == ["go", "java", "python", "typescript"]
    py = dict(e.examples)["python"]
    assert "for order in orders" in py.before and "in_bulk" in py.after


def test_javascript_uses_the_typescript_example() -> None:
    assert example_for("query-in-loop", "javascript") == example_for("query-in-loop", "typescript")
    assert example_for("query-in-loop", None) is None
    assert example_for("query-in-loop", "cobol") is None


def test_to_json_is_plain_data() -> None:
    j = explanation("query-in-loop").to_json()
    assert set(j) == {"what", "why", "fix", "examples"}
    assert set(j["examples"]["python"]) == {"before", "after"}  # type: ignore[index]


def test_bad_files_are_refused() -> None:
    with pytest.raises(ExplanationError, match="unknown key"):
        parse("x", {"what": "a", "why": "b", "fix": "c", "colour": "d"})
    with pytest.raises(ExplanationError, match="empty"):
        parse("x", {"what": " ", "why": "b", "fix": "c"})
    with pytest.raises(ExplanationError, match="language"):
        parse("x", {"what": "a", "why": "b", "fix": "c", "example": {"cobol": {"before": "a", "after": "b"}}})
    with pytest.raises(ExplanationError, match="before and after"):
        parse("x", {"what": "a", "why": "b", "fix": "c", "example": {"go": {"before": "a"}}})
    with pytest.raises(ExplanationError, match="no explanation"):
        explanation("no-such-check")


def test_required_languages_follow_the_packs() -> None:
    assert required_languages("complex-function") == {"python", "typescript", "go", "java"}
    assert required_languages("blocking-in-async") == {"python", "typescript"}
    assert required_languages("code-injection") == {"python", "typescript", "java"}
    assert required_languages("debug-enabled") == {"python", "go"}
    assert required_languages("module-cycle") == frozenset()


def test_example_text_is_dedented_and_trimmed() -> None:
    e = parse("x", {"what": "a", "why": "b", "fix": "c", "example": {"go": {"before": "\n  a\n    b\n", "after": "c\n"}}})
    assert dict(e.examples)["go"] == Example("a\n  b", "c")
    assert explain.LANGS == ("python", "typescript", "go", "java")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_explain.py -q`
Expected: FAIL at import (`svarupa.health.explain` does not exist).

- [ ] **Step 3: Write `svarupa/health/explain.py`**

```python
"""Plain explanations of the health checks: what a finding means, why it
matters and how to fix it, with a short before/after per language.

The text is fixed and lives in `explain/<check-id>.toml` next to this
module, so it is reviewed like code and never generated.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, cast

__all__ = [
    "LANGS",
    "Example",
    "Explanation",
    "ExplanationError",
    "example_for",
    "explanation",
    "parse",
    "required_languages",
]

LANGS = ("python", "typescript", "go", "java")
_KEYS = frozenset({"what", "why", "fix", "example"})
# A language whose pack shares another pack's rules shares its examples.
_SAME_AS = {"javascript": "typescript"}
_FUNCTION_CHECKS = frozenset(
    {"complex-function", "long-function", "many-parameters", "deep-nesting", "large-class"}
)
_SINK_CHECKS = {"sql-injection": "sql", "command-injection": "shell", "code-injection": "code"}


class ExplanationError(ValueError):
    """An explanation file that cannot be shown as written."""


@dataclass(frozen=True, slots=True)
class Example:
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class Explanation:
    check: str
    what: str
    why: str
    fix: str
    examples: tuple[tuple[str, Example], ...] = ()  # sorted by language

    def to_json(self) -> dict[str, object]:
        return {
            "what": self.what,
            "why": self.why,
            "fix": self.fix,
            "examples": {
                lang: {"before": ex.before, "after": ex.after} for lang, ex in self.examples
            },
        }


def _text(check: str, where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplanationError(f"{check}: {where} is empty")
    return value


def _code(text: str) -> str:
    return textwrap.dedent(text).strip("\n").rstrip()


def parse(check: str, data: dict[str, Any]) -> Explanation:
    """One explanation from its parsed TOML table."""
    unknown = set(data) - _KEYS
    if unknown:
        raise ExplanationError(f"{check}: unknown key {sorted(unknown)[0]!r}")
    examples: list[tuple[str, Example]] = []
    for lang, body in sorted(cast("dict[str, Any]", data.get("example", {})).items()):
        if lang not in LANGS:
            raise ExplanationError(f"{check}: unknown language {lang!r}")
        if not isinstance(body, dict) or set(cast("dict[str, Any]", body)) != {"before", "after"}:
            raise ExplanationError(f"{check}: example.{lang} needs before and after")
        pair = cast("dict[str, Any]", body)
        examples.append(
            (
                lang,
                Example(
                    _code(_text(check, f"example.{lang}.before", pair["before"])),
                    _code(_text(check, f"example.{lang}.after", pair["after"])),
                ),
            )
        )
    return Explanation(
        check,
        _text(check, "what", data.get("what")).strip(),
        _text(check, "why", data.get("why")).strip(),
        _text(check, "fix", data.get("fix")).strip(),
        tuple(examples),
    )


def _toml() -> Any:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib


@cache
def explanation(check_id: str) -> Explanation:
    path = resources.files("svarupa.health").joinpath("explain", f"{check_id}.toml")
    if not path.is_file():
        raise ExplanationError(f"{check_id}: no explanation file")
    data = _toml().loads(path.read_text(encoding="utf8"))
    return parse(check_id, cast("dict[str, Any]", data))


def example_for(check_id: str, lang: str | None) -> Example | None:
    if lang is None:
        return None
    return dict(explanation(check_id).examples).get(_SAME_AS.get(lang, lang))


def required_languages(check_id: str) -> frozenset[str]:
    """The languages whose pack can produce this check."""
    from svarupa.extract.packs import PACKS

    out: set[str] = set()
    for pack in PACKS:
        lang = _SAME_AS.get(pack.lang, pack.lang)
        if check_id in _FUNCTION_CHECKS and pack.metrics is not None:
            out.add(lang)
        r = pack.risks
        if r is None:
            continue
        if check_id in _SINK_CHECKS and any(s.tag == _SINK_CHECKS[check_id] for s in r.sinks):
            out.add(lang)
        if any(u.check == check_id for u in r.unsafe):
            out.add(lang)
        if (
            (check_id == "query-in-loop" and r.datastore_calls is not None)
            or (check_id == "blocking-in-async" and r.blocking is not None and r.async_marker)
            or (check_id == "swallowed-error" and r.catch_types)
            or (check_id == "endpoint-without-auth" and r.auth_markers)
        ):
            out.add(lang)
    return frozenset(out)
```

- [ ] **Step 4: Write `svarupa/health/explain/query-in-loop.toml`**

Use the content of `query-in-loop.toml` from Task 2's file list below (the same text; Task 2 writes the other 29).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_explain.py -q`
Expected: PASS.

- [ ] **Step 6: Lint, type check, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/health/explain.py svarupa/health/explain tests/test_explain.py
git commit -m "Load plain explanations of the health checks from TOML"
```

---

### Task 2: The 30 explanation files

**Files:**
- Create: `svarupa/health/explain/<check-id>.toml` for every catalog check (content below)
- Test: `tests/test_explain.py`

**Interfaces:**
- Consumes: `explanation`, `required_languages` (Task 1), `CATALOG`.

- [ ] **Step 1: Write the failing completeness tests** (append to `tests/test_explain.py`)

```python
from svarupa.health import CATALOG  # noqa: E402


def test_every_check_has_an_explanation() -> None:
    for check in CATALOG:
        explanation(check.id)


def test_every_language_that_can_fire_has_an_example() -> None:
    missing = [
        f"{c.id}:{lang}"
        for c in CATALOG
        for lang in sorted(required_languages(c.id))
        if example_for(c.id, lang) is None
    ]
    assert missing == []


def test_explanations_are_plain_text() -> None:
    for check in CATALOG:
        e = explanation(check.id)
        text = " ".join([e.what, e.why, e.fix, *(x.before + x.after for _, x in e.examples)])
        assert "—" not in text, check.id
        assert "TODO" not in text, check.id
        for part in (e.what, e.why, e.fix):
            assert len(part) <= 400, check.id
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_explain.py -q -k "every or plain"`
Expected: FAIL with `ExplanationError: complex-function: no explanation file`.

- [ ] **Step 3: Write the files**

Each block below is one file; the line before the fence names it. Write them exactly. (An executor may extract them with a short script that reads each `File:` line and the fenced block after it.)

File: `svarupa/health/explain/complex-function.toml`
```toml
what = "This function has many paths through it. Every if, loop, case and and/or adds one path; Svarupa counts them (this count is called cyclomatic complexity) and the limit is 10."
why = "Each path needs its own test, and a reader has to keep every path in mind to change the function safely. Bugs hide in the paths nobody tested."
fix = "Split the function. Move each separate decision into a small helper with a clear name, use a lookup table instead of long if/else chains, and return early instead of nesting."

[example.python]
before = '''
def shipping(order):
    if order.country == "US":
        if order.weight > 10:
            return 20
        return 10
    elif order.country == "CA":
        return 15
    else:
        return 30
'''
after = '''
RATES = {"US": 10, "CA": 15}

def shipping(order):
    if order.country == "US" and order.weight > 10:
        return 20
    return RATES.get(order.country, 30)
'''

[example.typescript]
before = '''
function shipping(order) {
  if (order.country === "US") {
    if (order.weight > 10) return 20;
    return 10;
  } else if (order.country === "CA") {
    return 15;
  } else {
    return 30;
  }
}
'''
after = '''
const RATES = { US: 10, CA: 15 };

function shipping(order) {
  if (order.country === "US" && order.weight > 10) return 20;
  return RATES[order.country] ?? 30;
}
'''

[example.go]
before = '''
func shipping(o Order) int {
	if o.Country == "US" {
		if o.Weight > 10 {
			return 20
		}
		return 10
	} else if o.Country == "CA" {
		return 15
	}
	return 30
}
'''
after = '''
var rates = map[string]int{"US": 10, "CA": 15}

func shipping(o Order) int {
	if o.Country == "US" && o.Weight > 10 {
		return 20
	}
	if r, ok := rates[o.Country]; ok {
		return r
	}
	return 30
}
'''

[example.java]
before = '''
int shipping(Order o) {
    if (o.country().equals("US")) {
        if (o.weight() > 10) return 20;
        return 10;
    } else if (o.country().equals("CA")) {
        return 15;
    } else {
        return 30;
    }
}
'''
after = '''
static final Map<String, Integer> RATES = Map.of("US", 10, "CA", 15);

int shipping(Order o) {
    if (o.country().equals("US") && o.weight() > 10) return 20;
    return RATES.getOrDefault(o.country(), 30);
}
'''
```

File: `svarupa/health/explain/long-function.toml`
```toml
what = "This function is longer than 50 lines."
why = "A long function usually does several jobs at once. It is hard to name, hard to test, and a change to one job can break another."
fix = "Find the separate steps (read the input, check it, do the work, save, reply) and move each step into its own function with a name that says what it does."

[example.python]
before = '''
def place_order(request):
    # 15 lines that read and check the form
    # 20 lines that price the cart
    # 15 lines that save and send the email
    ...
'''
after = '''
def place_order(request):
    form = read_order_form(request)
    total = price_cart(form.items)
    order = save_order(form, total)
    send_confirmation(order)
    return order
'''

[example.typescript]
before = '''
async function placeOrder(req) {
  // 15 lines that read and check the body
  // 20 lines that price the cart
  // 15 lines that save and send the email
}
'''
after = '''
async function placeOrder(req) {
  const form = readOrderForm(req);
  const total = priceCart(form.items);
  const order = await saveOrder(form, total);
  await sendConfirmation(order);
  return order;
}
'''

[example.go]
before = '''
func PlaceOrder(r *http.Request) (*Order, error) {
	// 15 lines that read and check the body
	// 20 lines that price the cart
	// 15 lines that save and send the email
}
'''
after = '''
func PlaceOrder(r *http.Request) (*Order, error) {
	form, err := readOrderForm(r)
	if err != nil {
		return nil, err
	}
	order, err := saveOrder(form, priceCart(form.Items))
	if err != nil {
		return nil, err
	}
	return order, sendConfirmation(order)
}
'''

[example.java]
before = '''
Order placeOrder(OrderRequest req) {
    // 15 lines that read and check the request
    // 20 lines that price the cart
    // 15 lines that save and send the email
}
'''
after = '''
Order placeOrder(OrderRequest req) {
    OrderForm form = readOrderForm(req);
    Money total = priceCart(form.items());
    Order order = saveOrder(form, total);
    sendConfirmation(order);
    return order;
}
'''
```

File: `svarupa/health/explain/many-parameters.toml`
```toml
what = "This function takes more than 5 parameters."
why = "Callers have to pass many values in the right order, and it is easy to swap two of them without any error. Long parameter lists often mean several values belong together."
fix = "Group the values that travel together into one object (a data class, an interface, a struct or a record) and pass that instead."

[example.python]
before = '''
def create_user(name, email, street, city, zip_code, country):
    ...
'''
after = '''
@dataclass
class Address:
    street: str
    city: str
    zip_code: str
    country: str

def create_user(name, email, address: Address):
    ...
'''

[example.typescript]
before = '''
function createUser(name, email, street, city, zip, country) { /* ... */ }
'''
after = '''
interface Address { street: string; city: string; zip: string; country: string }

function createUser(name: string, email: string, address: Address) { /* ... */ }
'''

[example.go]
before = '''
func CreateUser(name, email, street, city, zip, country string) error
'''
after = '''
type Address struct {
	Street, City, Zip, Country string
}

func CreateUser(name, email string, addr Address) error
'''

[example.java]
before = '''
User createUser(String name, String email, String street, String city, String zip, String country)
'''
after = '''
record Address(String street, String city, String zip, String country) {}

User createUser(String name, String email, Address address)
'''
```

File: `svarupa/health/explain/deep-nesting.toml`
```toml
what = "Blocks inside this function are nested more than 4 levels deep (an if inside a loop inside an if, and so on)."
why = "Every level is one more condition the reader has to remember. Deep code is hard to follow and hard to change without breaking a case."
fix = "Check the bad cases first and leave early (return, continue), so the main path stays at the left edge. Move inner blocks into helper functions."

[example.python]
before = '''
for order in orders:
    if order.paid:
        if order.items:
            for item in order.items:
                if item.in_stock:
                    ship(item)
'''
after = '''
for order in orders:
    if not order.paid or not order.items:
        continue
    ship_in_stock(order.items)

def ship_in_stock(items):
    for item in items:
        if item.in_stock:
            ship(item)
'''

[example.typescript]
before = '''
for (const order of orders) {
  if (order.paid) {
    if (order.items.length) {
      for (const item of order.items) {
        if (item.inStock) ship(item);
      }
    }
  }
}
'''
after = '''
for (const order of orders) {
  if (!order.paid || !order.items.length) continue;
  order.items.filter((i) => i.inStock).forEach(ship);
}
'''

[example.go]
before = '''
for _, o := range orders {
	if o.Paid {
		if len(o.Items) > 0 {
			for _, it := range o.Items {
				if it.InStock {
					ship(it)
				}
			}
		}
	}
}
'''
after = '''
for _, o := range orders {
	if !o.Paid || len(o.Items) == 0 {
		continue
	}
	shipInStock(o.Items)
}
'''

[example.java]
before = '''
for (Order o : orders) {
    if (o.paid()) {
        if (!o.items().isEmpty()) {
            for (Item it : o.items()) {
                if (it.inStock()) ship(it);
            }
        }
    }
}
'''
after = '''
for (Order o : orders) {
    if (!o.paid() || o.items().isEmpty()) continue;
    o.items().stream().filter(Item::inStock).forEach(this::ship);
}
'''
```

File: `svarupa/health/explain/large-file.toml`
```toml
what = "This file has more than 1000 lines."
why = "A big file usually mixes several topics. It is hard to find things in it, and many people editing it at once causes merge conflicts."
fix = "Split it by topic into smaller files in the same folder. Keep the old import path working (re-export the names) while callers move over."
```

File: `svarupa/health/explain/large-class.toml`
```toml
what = "This class does a lot: the complexity of all its methods added together is above 47 (a measure called weighted methods per class)."
why = "A class that knows and does too much becomes the place every change touches. It is hard to test alone and hard to reuse."
fix = "Find groups of methods that work on the same data and move each group into its own class. The old class can keep thin methods that call the new ones."

[example.python]
before = '''
class OrderService:
    def price(self, order): ...
    def apply_discount(self, order): ...
    def charge_card(self, order): ...
    def refund(self, order): ...
    def send_email(self, order): ...
'''
after = '''
class Pricing:
    def price(self, order): ...
    def apply_discount(self, order): ...

class Payments:
    def charge_card(self, order): ...
    def refund(self, order): ...

class OrderService:
    def __init__(self, pricing: Pricing, payments: Payments): ...
'''

[example.typescript]
before = '''
class OrderService {
  price(order) {}
  applyDiscount(order) {}
  chargeCard(order) {}
  refund(order) {}
  sendEmail(order) {}
}
'''
after = '''
class Pricing { price(order) {} applyDiscount(order) {} }
class Payments { chargeCard(order) {} refund(order) {} }

class OrderService {
  constructor(private pricing: Pricing, private payments: Payments) {}
}
'''

[example.go]
before = '''
type OrderService struct{}

func (s *OrderService) Price(o Order) Money    {}
func (s *OrderService) ChargeCard(o Order) error {}
func (s *OrderService) Refund(o Order) error     {}
func (s *OrderService) SendEmail(o Order) error  {}
'''
after = '''
type Pricing struct{}
type Payments struct{}

type OrderService struct {
	pricing  *Pricing
	payments *Payments
}
'''

[example.java]
before = '''
class OrderService {
    Money price(Order o) { }
    void chargeCard(Order o) { }
    void refund(Order o) { }
    void sendEmail(Order o) { }
}
'''
after = '''
class Pricing { Money price(Order o) { } }
class Payments { void chargeCard(Order o) { } void refund(Order o) { } }

class OrderService {
    private final Pricing pricing;
    private final Payments payments;
}
'''
```

File: `svarupa/health/explain/duplicated-block.toml`
```toml
what = "The same 10 or more lines appear in more than one place."
why = "When the logic changes, someone has to find and fix every copy. Usually one copy gets missed and the copies start to behave differently."
fix = "Move the repeated lines into one function (or one shared module) and call it from each place. If the copies differ a little, pass the difference in as a parameter."
```

File: `svarupa/health/explain/module-cycle.toml`
```toml
what = "Two or more modules import each other, directly or through other modules, so they form a loop."
why = "You cannot change, test or reuse one module without the others, and import-order errors can appear at startup. Cycles also make the design hard to see."
fix = "Move the part both sides need into a third module that both import, or let one side depend on an interface (a small contract) instead of the other module itself."
```

File: `svarupa/health/explain/hub-module.toml`
```toml
what = "This module is used by 10 or more modules and itself uses 10 or more. Everything flows through it."
why = "A change here can break many places at once, and it changes often because so much depends on it. It becomes the bottleneck for the whole team."
fix = "Split it by responsibility so each caller only imports the small part it needs. Keep a thin entry that re-exports the parts while callers move over."
```

File: `svarupa/health/explain/layer-direction.toml`
```toml
what = "A module imports a layer it should not, according to this unit's design style (for example, the database layer imports the web layer)."
why = "Layers keep each concern replaceable. When a lower layer reaches up, a change in the web code can break data access, and the layers can no longer be tested alone."
fix = "Move the shared code down into the lower layer, or pass the value in from above as a parameter. The style's rules are listed in docs/design.md."
```

File: `svarupa/health/explain/part-independence.toml`
```toml
what = "Two parts that this unit's design style says must be independent import each other (for example, two features of a modular monolith)."
why = "Independent parts can be changed, owned and later split out on their own. One import between them ties their release and testing together."
fix = "Move what both parts need into a shared module, or let one part call the other through its public entry or an event instead of importing its inside."
```

File: `svarupa/health/explain/public-entry.toml`
```toml
what = "Code imports the inside of another part instead of going through that part's public entry (for example its __init__ or index file)."
why = "The inside of a part can change at any time. Importing it directly means a refactor inside one part breaks code in another."
fix = "Import from the part's public entry, and add the name you need to that entry if it is missing."
```

File: `svarupa/health/explain/core-purity.toml`
```toml
what = "The core of this unit (the business rules) imports a framework, a database driver or other input and output code."
why = "The core is meant to be plain logic that can be tested fast and kept when the framework changes. A framework import inside it removes both benefits."
fix = "Define a small interface in the core for what it needs (for example save_order), and implement that interface in an adapter outside the core."
```

File: `svarupa/health/explain/hardcoded-secret.toml`
```toml
what = "A password, key or token is written into a committed file. Svarupa shows only where it is and a fingerprint, never the value."
why = "Anyone who can read the repository, its forks, backups or build logs can use the secret. Deleting the line later does not help: it stays in git history."
fix = "Rotate the secret first (create a new one and turn the old one off). Then read it from an environment variable or a secret manager, and keep only placeholders in committed files."

[example.python]
before = '''
API_KEY = "<a real key>"
'''
after = '''
import os

API_KEY = os.environ["API_KEY"]
'''
```

File: `svarupa/health/explain/sql-injection.toml`
```toml
what = "A SQL query is built by joining strings, and part of it comes from data known only at runtime."
why = "If that data comes from a user, they can change the query itself: read other people's data, change it or delete it. This is one of the most common real attacks."
fix = "Keep the query text fixed and pass the values separately as query parameters. The database driver then sends them as data, never as SQL."

[example.python]
before = '''
cur.execute(f"SELECT * FROM users WHERE email = '{email}'")
'''
after = '''
cur.execute("SELECT * FROM users WHERE email = %s", (email,))
'''

[example.typescript]
before = '''
await db.query(`SELECT * FROM users WHERE email = '${email}'`);
'''
after = '''
await db.query("SELECT * FROM users WHERE email = $1", [email]);
'''

[example.go]
before = '''
db.Query("SELECT * FROM users WHERE email = '" + email + "'")
'''
after = '''
db.Query("SELECT * FROM users WHERE email = $1", email)
'''

[example.java]
before = '''
stmt.executeQuery("SELECT * FROM users WHERE email = '" + email + "'");
'''
after = '''
PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE email = ?");
ps.setString(1, email);
ps.executeQuery();
'''
```

File: `svarupa/health/explain/command-injection.toml`
```toml
what = "A shell command is built by joining strings, and part of it comes from data known only at runtime."
why = "A value like \"x; rm -rf /\" turns into a second command. Whoever controls the value can run any program on the server."
fix = "Run the program directly with its arguments as a list, without a shell. Then each value is one argument, never extra commands."

[example.python]
before = '''
os.system("convert " + filename + " out.png")
'''
after = '''
subprocess.run(["convert", filename, "out.png"], check=True)
'''

[example.typescript]
before = '''
child_process.exec("convert " + filename + " out.png");
'''
after = '''
child_process.execFile("convert", [filename, "out.png"]);
'''

[example.go]
before = '''
exec.Command("sh", "-c", "convert "+filename+" out.png").Run()
'''
after = '''
exec.Command("convert", filename, "out.png").Run()
'''

[example.java]
before = '''
Runtime.getRuntime().exec("convert " + filename + " out.png");
'''
after = '''
new ProcessBuilder("convert", filename, "out.png").start();
'''
```

File: `svarupa/health/explain/code-injection.toml`
```toml
what = "Code is built from strings and then run (eval, exec, new Function, an expression parser)."
why = "If any part of the string comes from outside, whoever controls it can run their own code inside your program, with all its access."
fix = "Do not run built code. Parse the value as data (a number, JSON), or look the action up in a fixed table of allowed choices."

[example.python]
before = '''
result = eval("1 + " + user_value)
'''
after = '''
result = 1 + int(user_value)
'''

[example.typescript]
before = '''
const handler = new Function("return " + userValue);
'''
after = '''
const HANDLERS = { add: (a, b) => a + b, sub: (a, b) => a - b };
const handler = HANDLERS[userValue];
'''

[example.java]
before = '''
parser.parseExpression("price * " + userValue).getValue(order);
'''
after = '''
EvaluationContext ctx = new StandardEvaluationContext(order);
ctx.setVariable("factor", Double.parseDouble(userValue));
parser.parseExpression("price * #factor").getValue(ctx);
'''
```

File: `svarupa/health/explain/tls-verify-off.toml`
```toml
what = "A connection uses HTTPS (TLS) but turns off the check that the server's certificate is real."
why = "Without that check, anyone on the network path (public Wi-Fi, a compromised router) can pretend to be the server and read or change everything sent, including passwords."
fix = "Turn the check back on. If the server uses a private certificate, give the client that certificate authority instead of skipping the check."

[example.python]
before = '''
requests.get(url, verify=False, timeout=10)
'''
after = '''
requests.get(url, verify="/etc/ssl/company-ca.pem", timeout=10)
'''

[example.typescript]
before = '''
https.request({ host, rejectUnauthorized: false });
'''
after = '''
https.request({ host, ca: fs.readFileSync("company-ca.pem") });
'''

[example.go]
before = '''
cfg := &tls.Config{InsecureSkipVerify: true}
'''
after = '''
pool := x509.NewCertPool()
pool.AppendCertsFromPEM(caPEM)
cfg := &tls.Config{RootCAs: pool}
'''

[example.java]
before = '''
SSLConnectionSocketFactory f = new SSLConnectionSocketFactory(ctx, new NoopHostnameVerifier());
'''
after = '''
SSLConnectionSocketFactory f = new SSLConnectionSocketFactory(ctx);
'''
```

File: `svarupa/health/explain/unsafe-deserialize.toml`
```toml
what = "Data is turned back into objects with a format that can create any object (pickle, yaml.load without a safe loader, Java ObjectInputStream, node-serialize)."
why = "A crafted input can make the program run code while it is being read. If the data can come from a user, a cache or a queue, that is a remote takeover."
fix = "Use a data-only format such as JSON, or the safe variant of the loader, and check the result against the shape you expect."

[example.python]
before = '''
profile = pickle.loads(request.body)
'''
after = '''
profile = json.loads(request.body)
'''

[example.typescript]
before = '''
const profile = serialize.unserialize(req.body);
'''
after = '''
const profile = JSON.parse(req.body);
'''

[example.java]
before = '''
Profile p = (Profile) new ObjectInputStream(in).readObject();
'''
after = '''
Profile p = new ObjectMapper().readValue(in, Profile.class);
'''
```

File: `svarupa/health/explain/weak-hash.toml`
```toml
what = "MD5 or SHA-1 is used. Both are broken: people can create two different inputs with the same hash."
why = "If the hash protects passwords, signatures or tokens, an attacker can forge or crack them. For passwords, any fast hash is too weak."
fix = "Use SHA-256 for checksums and signatures. For passwords use a slow password hash (bcrypt, scrypt or Argon2). If the hash is only a cache key, mark it as not for security."

[example.python]
before = '''
digest = hashlib.md5(data).hexdigest()
'''
after = '''
digest = hashlib.sha256(data).hexdigest()
'''

[example.typescript]
before = '''
crypto.createHash("md5").update(data).digest("hex");
'''
after = '''
crypto.createHash("sha256").update(data).digest("hex");
'''

[example.go]
before = '''
sum := md5.Sum(data)
'''
after = '''
sum := sha256.Sum256(data)
'''

[example.java]
before = '''
MessageDigest md = MessageDigest.getInstance("MD5");
'''
after = '''
MessageDigest md = MessageDigest.getInstance("SHA-256");
'''
```

File: `svarupa/health/explain/debug-enabled.toml`
```toml
what = "Debug mode is turned on in code that can run in production."
why = "Debug pages show stack traces, settings and sometimes an interactive console to anyone who triggers an error. That leaks secrets and can let them run code."
fix = "Read the setting from the environment and make it off by default, so only a developer's machine turns it on."

[example.python]
before = '''
DEBUG = True
'''
after = '''
DEBUG = os.environ.get("DEBUG") == "1"
'''

[example.go]
before = '''
gin.SetMode(gin.DebugMode)
'''
after = '''
gin.SetMode(os.Getenv("GIN_MODE"))  // "release" unless set
'''
```

File: `svarupa/health/explain/cors-any-origin.toml`
```toml
what = "The server allows browsers on every website (origin \"*\") to call it."
why = "Any page a user visits can then talk to your API from their browser. Together with cookies or tokens, that can expose their data to other sites."
fix = "List the exact sites that may call the API (your own front end), and keep that list in configuration."

[example.python]
before = '''
app.add_middleware(CORSMiddleware, allow_origins=["*"])
'''
after = '''
app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.com"])
'''

[example.typescript]
before = '''
app.use(cors());
'''
after = '''
app.use(cors({ origin: ["https://app.example.com"] }));
'''

[example.go]
before = '''
cors.New(cors.Config{AllowAllOrigins: true})
'''
after = '''
cors.New(cors.Config{AllowOrigins: []string{"https://app.example.com"}})
'''

[example.java]
before = '''
registry.addMapping("/**").allowedOrigins("*");
'''
after = '''
registry.addMapping("/**").allowedOrigins("https://app.example.com");
'''
```

File: `svarupa/health/explain/endpoint-without-auth.toml`
```toml
what = "An endpoint that changes data (POST, PUT, PATCH or DELETE) has no sign of a login or permission check, while other endpoints in the same app have one."
why = "Anyone who finds the URL can call it. Missing access control is the most common web security problem (OWASP A01)."
fix = "Add the same auth check the other endpoints use. If the endpoint is public on purpose (a signed webhook), record that as an exception with a reason in .svarupa/design.yaml."

[example.python]
before = '''
@router.post("/orders")
def create_order(order: OrderIn):
    ...
'''
after = '''
@router.post("/orders")
def create_order(order: OrderIn, user: User = Depends(get_current_user)):
    ...
'''

[example.typescript]
before = '''
router.post("/orders", createOrder);
'''
after = '''
router.post("/orders", requireAuth, createOrder);
'''

[example.java]
before = '''
@PostMapping("/orders")
Order create(@RequestBody OrderIn in) { }
'''
after = '''
@PostMapping("/orders")
@PreAuthorize("isAuthenticated()")
Order create(@RequestBody OrderIn in) { }
'''
```

File: `svarupa/health/explain/shared-datastore.toml`
```toml
what = "Services built from two or more different codebases use the same database or store."
why = "Each service can change the shared tables in ways the others do not expect, a slow query from one slows all of them, and none of them can be released alone."
fix = "Give each service its own database (or schema) and let the others ask through the owning service's API or events. If the sharing is on purpose for now, record it as an exception with a reason."
```

File: `svarupa/health/explain/query-in-loop.toml`
```toml
what = "The same database query runs once for every item in a loop."
why = "100 items means 100 round trips to the database. Pages get slow as data grows, and the database gets busy for everyone. This is called the N+1 query problem."
fix = "Load everything the loop needs in one query before the loop (or let the ORM join it in), then look items up in memory."

[example.python]
before = '''
for order in orders:
    customer = Customer.objects.get(id=order.customer_id)
'''
after = '''
customers = Customer.objects.in_bulk([o.customer_id for o in orders])
for order in orders:
    customer = customers[order.customer_id]
'''

[example.typescript]
before = '''
for (const order of orders) {
  const customer = await prisma.customer.findUnique({ where: { id: order.customerId } });
}
'''
after = '''
const customers = await prisma.customer.findMany({
  where: { id: { in: orders.map((o) => o.customerId) } },
});
const byId = new Map(customers.map((c) => [c.id, c]));
'''

[example.go]
before = '''
for _, o := range orders {
	db.QueryRow("SELECT name FROM customers WHERE id = $1", o.CustomerID)
}
'''
after = '''
rows, err := db.Query("SELECT id, name FROM customers WHERE id = ANY($1)", pq.Array(ids))
'''

[example.java]
before = '''
for (Order o : orders) {
    Customer c = customerRepository.findById(o.customerId()).orElseThrow();
}
'''
after = '''
Map<Long, Customer> byId = customerRepository.findAllById(ids).stream()
    .collect(Collectors.toMap(Customer::id, c -> c));
'''
```

File: `svarupa/health/explain/blocking-in-async.toml`
```toml
what = "An async function calls something that blocks: it waits for the network, the disk or a timer without giving control back."
why = "Async code shares one thread. While one call blocks, every other request on that server waits too, so one slow call makes the whole service slow."
fix = "Use the async version of the library (httpx.AsyncClient, asyncio.sleep, fs.promises), or move the blocking call to a worker thread."

[example.python]
before = '''
async def fetch_rate(url):
    return requests.get(url, timeout=5).json()
'''
after = '''
async def fetch_rate(url):
    async with httpx.AsyncClient(timeout=5) as client:
        return (await client.get(url)).json()
'''

[example.typescript]
before = '''
async function loadConfig() {
  return JSON.parse(fs.readFileSync("config.json", "utf8"));
}
'''
after = '''
async function loadConfig() {
  return JSON.parse(await fs.promises.readFile("config.json", "utf8"));
}
'''
```

File: `svarupa/health/explain/hub-on-request-path.toml`
```toml
what = "A hub module (one that many modules use and that uses many) sits on the path of every HTTP request: every module with routes reaches it through its imports."
why = "Every endpoint depends on it, so one slow or broken change there affects all of them at once, and it is hard to scale or change any endpoint alone."
fix = "Split the hub so each route module imports only the small part it needs, and keep heavy work (setup, caches) out of the code that every request loads."
```

File: `svarupa/health/explain/no-timeout.toml`
```toml
what = "A call to another server has no timeout, so it can wait forever."
why = "When the other server hangs, your requests hang too, threads or connections run out, and one slow dependency takes your service down with it."
fix = "Set a timeout on every outbound call, a few seconds for most APIs, and decide what to do when it fires (retry, fall back or report an error)."

[example.python]
before = '''
r = requests.post(url, json=payload)
'''
after = '''
r = requests.post(url, json=payload, timeout=5)
'''

[example.typescript]
before = '''
const res = await axios.get(url);
'''
after = '''
const res = await axios.get(url, { timeout: 5000 });
'''

[example.go]
before = '''
resp, err := http.Get(url)
'''
after = '''
client := &http.Client{Timeout: 5 * time.Second}
resp, err := client.Get(url)
'''

[example.java]
before = '''
HttpClient client = HttpClient.newHttpClient();
'''
after = '''
HttpClient client = HttpClient.newBuilder()
    .connectTimeout(Duration.ofSeconds(5))
    .build();
'''
```

File: `svarupa/health/explain/swallowed-error.toml`
```toml
what = "An error is caught and then ignored: the handler is empty or only says pass or has a comment."
why = "The program goes on as if nothing happened, with bad or missing data. When something breaks later, there is no log to show where it started."
fix = "Handle the error, log it with enough detail to debug, or let it go up to a caller that can. If ignoring it is right, catch the narrowest error and write why in a comment next to a log line."

[example.python]
before = '''
try:
    send_email(user)
except Exception:
    pass
'''
after = '''
try:
    send_email(user)
except SMTPException:
    log.warning("welcome email failed for user %s", user.id, exc_info=True)
'''

[example.typescript]
before = '''
try {
  await sendEmail(user);
} catch (e) {}
'''
after = '''
try {
  await sendEmail(user);
} catch (e) {
  logger.warn({ err: e, userId: user.id }, "welcome email failed");
}
'''

[example.go]
before = '''
if err := sendEmail(user); err != nil {
}
'''
after = '''
if err := sendEmail(user); err != nil {
	log.Printf("welcome email failed for user %d: %v", user.ID, err)
}
'''

[example.java]
before = '''
try {
    sendEmail(user);
} catch (MessagingException e) {
}
'''
after = '''
try {
    sendEmail(user);
} catch (MessagingException e) {
    log.warn("welcome email failed for user {}", user.id(), e);
}
'''
```

File: `svarupa/health/explain/no-retry.toml`
```toml
what = "A connection to a queue or store is set up so it tries only once, or with its retries turned off."
why = "Networks drop for a moment all the time. Without a retry, a short blip becomes a failed job or a crashed worker."
fix = "Turn on the client's retry or reconnect option with a small limit and a growing delay between tries."

[example.python]
before = '''
params = pika.ConnectionParameters(host="mq")
'''
after = '''
params = pika.ConnectionParameters(host="mq", connection_attempts=5, retry_delay=2)
'''

[example.go]
before = '''
rdb := redis.NewClient(&redis.Options{Addr: addr, MaxRetries: -1})
'''
after = '''
rdb := redis.NewClient(&redis.Options{Addr: addr, MaxRetries: 3})
'''

[example.java]
before = '''
factory.setAutomaticRecoveryEnabled(false);
'''
after = '''
factory.setAutomaticRecoveryEnabled(true);
factory.setNetworkRecoveryInterval(5000);
'''
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_explain.py -q`
Expected: PASS.

- [ ] **Step 5: Check the wheel ships the files**

Run: `uv build --wheel -o /tmp/svw >/dev/null 2>&1; unzip -l /tmp/svw/*.whl | grep -c "svarupa/health/explain/.*\.toml"`
Expected: `30`.

- [ ] **Step 6: Commit**

```bash
git add svarupa/health/explain tests/test_explain.py
git commit -m "Write plain explanations for every health check"
```

---

### Task 3: Explanations in graph.json, get_health and `svarupa explain`

**Files:**
- Modify: `svarupa/health/model.py` (`Health.to_json`)
- Modify: `svarupa/cli.py` (`main` dispatch, new `_explain`)
- Test: `tests/test_explain.py`, `tests/test_health_outputs.py`

**Interfaces:**
- Consumes: `explanation`, `example_for`, `LANGS` (Task 1).
- Produces: `health.checks[i]["explanation"]` in graph.json; CLI `svarupa explain [check] [--lang L]` (exit 0; unknown check exit 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_health_outputs.py`:

```python
def test_explanations_reach_graph_json_and_get_health(tmp_path: Path, capsys) -> None:
    make_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    checks = {c["id"]: c for c in gj["health"]["checks"]}
    assert checks["many-parameters"]["explanation"]["what"].startswith("This function takes")
    assert "python" in checks["many-parameters"]["explanation"]["examples"]
    assert "explanation" not in gj["health"]["violations"][0]
    assert main(["query", str(tmp_path / ".svarupa"), "get_health", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {c["id"]: c for c in result["checks"]}["many-parameters"]["explanation"]["fix"]
```

Append to `tests/test_explain.py`:

```python
from svarupa.cli import main  # noqa: E402


def test_explain_lists_the_checks(capsys) -> None:
    assert main(["explain"]) == 0
    out = capsys.readouterr().out
    assert "query-in-loop" in out and "Query in a loop (N+1)" in out
    assert len(out.strip().splitlines()) == len(CATALOG)


def test_explain_prints_one_check_in_one_language(capsys) -> None:
    assert main(["explain", "query-in-loop", "--lang", "javascript"]) == 0
    out = capsys.readouterr().out
    assert "What:" in out and "Why:" in out and "How to fix:" in out
    assert "prisma.customer.findMany" in out
    assert "in_bulk" not in out


def test_explain_without_an_example_prints_the_text(capsys) -> None:
    assert main(["explain", "debug-enabled", "--lang", "java"]) == 0
    out = capsys.readouterr().out
    assert "How to fix:" in out and "no java example" in out


def test_explain_unknown_check_names_close_ones(capsys) -> None:
    assert main(["explain", "query-in-loops"]) == 2
    err = capsys.readouterr().err
    assert "unknown check" in err and "query-in-loop" in err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_explain.py tests/test_health_outputs.py -q -k "explain or explanations"`
Expected: FAIL (`KeyError: 'explanation'`; `explain` treated as a path).

- [ ] **Step 3: Attach explanations in `Health.to_json`** (`svarupa/health/model.py`)

Replace the inner import line `        from svarupa.health.catalog import CATALOG` with:

```python
        from svarupa.health.catalog import CATALOG
        from svarupa.health.explain import explanation
```

and add to each check dict, after `"maturity": c.maturity,`:

```python
                    "explanation": explanation(c.id).to_json(),
```

- [ ] **Step 4: Add `svarupa explain`** (`svarupa/cli.py`)

In `main`, before `if argv[:1] == ["design"]:`, add:

```python
        if argv[:1] == ["explain"]:
            return _explain(argv[1:])
```

In the epilog string, after the `svarupa design` sentence, add `'"svarupa explain <check>" says what a health check means and how to fix it; '`.

Add the function before `def main(`:

```python
def _explain(argv: list[str]) -> int:
    """What a health check means, why it matters, how to fix it."""
    import difflib

    from svarupa.health import BY_ID, CATALOG
    from svarupa.health.explain import LANGS, example_for, explanation

    parser = argparse.ArgumentParser(
        prog="svarupa explain",
        description="Explain a health check in plain words, with a before/after example.",
    )
    parser.add_argument("check", nargs="?", help="check id, for example query-in-loop")
    parser.add_argument(
        "--lang", default=None, help=f"show only this language's example ({', '.join(LANGS)}, javascript)"
    )
    args = parser.parse_args(argv)
    if args.check is None:
        for c in CATALOG:
            print(f"{c.id:<24} {c.title}")
        return 0
    if args.check not in BY_ID:
        close = difflib.get_close_matches(args.check, list(BY_ID), n=3)
        hint = f"; did you mean: {', '.join(close)}" if close else ""
        print(f"svarupa explain: unknown check {args.check!r}{hint}", file=sys.stderr)
        return 2
    check, e = BY_ID[args.check], explanation(args.check)
    print(f"{check.id}: {check.title} [{check.area}, {check.severity}]")
    print(f"Source: {check.source}\n")
    print(f"What: {e.what}\nWhy: {e.why}\nHow to fix: {e.fix}")
    if args.lang is not None:
        ex = example_for(args.check, args.lang)
        shown = [(args.lang, ex)] if ex is not None else []
        if ex is None:
            print(f"\n(no {args.lang} example for this check)")
    else:
        shown = list(e.examples)
    for lang, ex in shown:
        print(f"\nBefore ({lang}):\n{textwrap.indent(ex.before, '    ')}")
        print(f"After:\n{textwrap.indent(ex.after, '    ')}")
    return 0
```

Add `import textwrap` to the module imports if it is not there.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_explain.py tests/test_health_outputs.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/health/model.py svarupa/cli.py tests
git commit -m "Show check explanations in graph.json and svarupa explain"
```

Expected: suite PASS.

---

### Task 4: Explanations in REPORT.md and the viewer

**Files:**
- Create: `svarupa/emit/explained.py`
- Modify: `svarupa/emit/report.py` (`_health_section` and its caller)
- Modify: `svarupa/emit/viewer.py` (`_health_tab`, `render_viewer`, the script)
- Test: `tests/test_health_outputs.py`

**Interfaces:**
- Consumes: `explanation`, `example_for` (Task 1); `Graph.nodes[path].lang`.
- Produces: `file_lang(graph: Graph | None, path: str) -> str | None`; `explanation_markdown(check_id: str, lang: str | None) -> list[str]`; `checks_in_order(health: Health) -> list[tuple[str, str | None]]` (check id, language of its first violation's file, needs graph).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_health_outputs.py`)

```python
def test_report_and_viewer_explain_the_checks(tmp_path: Path) -> None:
    make_repo(tmp_path)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "m.js").write_text(
        "function f(a, b, c, d, e, g) {\n  return a;\n}\n", encoding="utf8"
    )
    assert main([str(tmp_path)]) == 0
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "### What these mean" in report
    assert "#### Too many parameters (`many-parameters`)" in report
    assert report.count("#### Too many parameters") == 1
    assert "How to fix:" in report
    assert "```python" in report  # the first many-parameters violation is app/m.py
    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert 'id="why-many-parameters"' in html
    assert html.count('id="why-many-parameters"') == 1
    assert 'data-why="many-parameters"' in html
    assert "This function takes more than 5 parameters." in html


def test_a_javascript_finding_shows_the_typescript_example(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "m.js").write_text(
        "function f(a, b, c, d, e, g) {\n  return a;\n}\n", encoding="utf8"
    )
    assert main([str(tmp_path)]) == 0
    report = (tmp_path / ".svarupa" / "REPORT.md").read_text(encoding="utf8")
    assert "```javascript" in report and "interface Address" in report
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_health_outputs.py -q -k "explain or typescript_example"`
Expected: FAIL (`### What these mean` missing).

- [ ] **Step 3: Write `svarupa/emit/explained.py`**

```python
"""Check explanations, laid out for the report and the viewer."""

from __future__ import annotations

from svarupa.build import Graph
from svarupa.health import BY_ID, Health
from svarupa.health.explain import example_for, explanation

__all__ = ["checks_in_order", "explanation_markdown", "file_lang"]


def file_lang(graph: Graph | None, path: str) -> str | None:
    """The language Svarupa parsed this file as, if it parsed it."""
    node = graph.nodes.get(path) if graph is not None else None
    return node.lang if node is not None else None


def checks_in_order(health: Health, graph: Graph | None) -> list[tuple[str, str | None]]:
    """Each check with violations, in the order its first violation is
    listed, with the language of that violation's file."""
    seen: dict[str, str | None] = {}
    for v in health.violations:
        if v.check not in seen:
            seen[v.check] = file_lang(graph, v.evidence[0].file)
    return list(seen.items())


def explanation_markdown(check_id: str, lang: str | None) -> list[str]:
    e = explanation(check_id)
    lines = [
        f"#### {BY_ID[check_id].title} (`{check_id}`)",
        "",
        f"What: {e.what}",
        "",
        f"Why: {e.why}",
        "",
        f"How to fix: {e.fix}",
        "",
    ]
    ex = example_for(check_id, lang)
    if ex is not None and lang is not None:
        lines += [f"Before ({lang}):", "", f"```{lang}", ex.before, "```", "", "After:", ""]
        lines += [f"```{lang}", ex.after, "```", ""]
    return lines
```

- [ ] **Step 4: The report section** (`svarupa/emit/report.py`)

Change the signature to `def _health_section(health: Health | None, graph: Graph | None = None) -> list[str]:` and its call in `render_report` to `_health_section(health, graph)`. Add `from svarupa.emit.explained import checks_in_order, explanation_markdown` to the imports (and `Graph` if not imported). Right before `lines += ["", "### All violations", ""]`, add:

```python
    lines += ["", "### What these mean", ""]
    for check_id, lang in checks_in_order(health, graph):
        lines += explanation_markdown(check_id, lang)
```

- [ ] **Step 5: The viewer section** (`svarupa/emit/viewer.py`)

Add `from svarupa.emit.explained import checks_in_order, file_lang` and `from svarupa.health.explain import example_for, explanation` to the imports. Change `_health_tab(health, design)` to `_health_tab(health, design, graph=None)` and its call in `render_viewer` to `_health_tab(health, design, graph)`. In `items`, after the `esc(f" {BY_ID[v.check].title}: ...")` element, add:

```python
                    esc(" "),
                    tag("a", esc("why?"), href="#d-health", class_="why-link", data_why=v.check),
```

(`attrs` turns `data_why` into `data-why`.)

Add the folds builder above `_health_tab`:

```python
def _why_folds(health: Health, graph: Graph | None) -> Markup:
    """One closed fold per check with violations: what, why, fix, example."""
    folds = []
    for check_id, lang in checks_in_order(health, graph):
        e = explanation(check_id)
        ex = example_for(check_id, lang)
        body = [
            tag("summary", esc(BY_ID[check_id].title)),
            tag("p", join((tag("strong", esc("What ")), esc(e.what)))),
            tag("p", join((tag("strong", esc("Why ")), esc(e.why)))),
            tag("p", join((tag("strong", esc("How to fix ")), esc(e.fix)))),
        ]
        if ex is not None:
            body += [
                tag("p", esc(f"Before ({lang})"), class_="meta"),
                tag("pre", tag("code", esc(ex.before))),
                tag("p", esc("After"), class_="meta"),
                tag("pre", tag("code", esc(ex.after))),
            ]
        folds.append(tag("details", join(body), id=f"why-{check_id}", class_="why"))
    return join(folds)
```

In `_health_tab`'s section, after the violations list, add:

```python
                tag("h3", esc("What these mean")) if health.violations else raw(""),
                _why_folds(health, graph) if health.violations else raw(""),
```

In the viewer script, after the panel element lookups (`var linkBtn = ...`), add:

```javascript
  // "why?" opens that check's fold in the Health tab. The tab switch is the
  // link's own #d-health; the fold is opened here.
  function openWhy(check) {
    var fold = document.getElementById('why-' + check);
    if (!fold) return;
    fold.open = true;
    fold.scrollIntoView();
  }
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('[data-why]');
    if (a) setTimeout(function () { openWhy(a.getAttribute('data-why')); }, 0);
  });
```

Add CSS next to the other Health tab rules: `details.why { margin: .4rem 0; } details.why pre { overflow-x: auto; }`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_health_outputs.py tests/test_emit.py -q`
Expected: PASS (the emit suite also checks the script has no `<`).

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/emit tests
git commit -m "Explain each check in REPORT.md and the viewer"
```

---

### Task 5: One plain line per box

**Files:**
- Create: `svarupa/derive/about.py`
- Test: `tests/test_about.py`

**Interfaces:**
- Consumes: `Graph` (`routes`, `tasks`, `entrypoints`, `externals`, `module_deps`, `modules`, `nodes`, `edges`, `architecture_paths`), `modules_shipped`, `Health.violations`, `BY_ID`, `SEVERITIES`, `DiagramSet.specs` (mapping of spec id to `DiagramSpec` with `.nodes`).
- Produces: `About(text: str, worst: str | None)`, frozen dataclass; `about_lines(graph: Graph, health: Health | None, produced: Mapping[DiagramKind, DiagramSet]) -> dict[str, About]` keyed by box id (`.` for the root module).

- [ ] **Step 1: Write the failing tests** (create `tests/test_about.py`)

```python
"""One plain sentence per diagram box, with its health."""

from __future__ import annotations

from pathlib import Path

from svarupa.build import build
from svarupa.cluster import cluster
from svarupa.derive import derive_all
from svarupa.derive.about import About, about_lines
from svarupa.detect import detect
from svarupa.extract import declared_dependencies, extract
from svarupa.health import assess, source_texts
from tests.test_health import write


def lines(root: Path, with_health: bool = True) -> dict[str, About]:
    scan = detect(root)
    graph = build(scan, extract(scan, declared_dependencies(scan)), strict=False)
    health = assess(graph, source_texts(scan, graph)) if with_health else None
    produced, _ = derive_all(graph, cluster(graph))
    return about_lines(graph, health, produced)


def api_repo(root: Path) -> None:
    write(
        root,
        "api/routes.py",
        "from fastapi import APIRouter\nfrom domain import orders\nimport psycopg2\n"
        "router = APIRouter()\n\n@router.get('/orders')\ndef a():\n    return 1\n\n"
        "@router.post('/orders')\ndef b():\n    return 2\n",
    )
    write(root, "domain/orders.py", "def x(a, b, c, d, e, f):\n    return a\n")
    write(root, "util/x.py", "VALUE = 1\n")


def test_a_module_line_names_its_routes_deps_and_externals(tmp_path: Path) -> None:
    api_repo(tmp_path)
    text = lines(tmp_path)["api"].text
    assert text.startswith("Module api: serves 2 HTTP routes (FastAPI)")
    assert "uses 1 module and is used by 0" in text
    assert "talks to PostgreSQL" in text
    assert text.endswith("No health findings.")


def test_a_module_with_no_facts_reads_its_id(tmp_path: Path) -> None:
    api_repo(tmp_path)
    assert lines(tmp_path, with_health=False)["util"].text == "Module util."


def test_the_health_clause_names_the_worst_finding(tmp_path: Path) -> None:
    api_repo(tmp_path)
    a = lines(tmp_path)["domain"]
    assert a.text.endswith("1 health finding, worst: Too many parameters (minor).")
    assert a.worst == "many-parameters"


def test_root_module_box_counts_root_findings(tmp_path: Path) -> None:
    write(tmp_path, "main.py", "def x(a, b, c, d, e, f):\n    return a\n")
    write(tmp_path, "pkg/m.py", "from main import x\n")
    got = lines(tmp_path)
    assert "." in got
    assert got["."].worst == "many-parameters"


def test_an_external_box_says_who_uses_it(tmp_path: Path) -> None:
    api_repo(tmp_path)
    ext = next(v for k, v in lines(tmp_path).items() if k.startswith("ext:"))
    assert ext.text.startswith("External database (PostgreSQL), used from 1 module.")


def test_compose_boxes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  api:\n    build: ./api\n    depends_on: [db]\n"
        "  admin:\n    image: adminer\n  db:\n    image: postgres:16\n",
    )
    api_repo(tmp_path)
    got = {k: v.text for k, v in lines(tmp_path).items()}
    svc = next(t for k, t in got.items() if k.endswith("#service.api"))
    assert svc.startswith("Service api: built from ./api, depends on db.")
    adm = next(t for k, t in got.items() if k.endswith("#service.admin"))
    assert adm.startswith("Service admin: runs the image adminer.")
    db = next(t for k, t in got.items() if k.endswith("#service.db"))
    assert db.startswith("PostgreSQL database (compose service db), used by api.")
```

(`derive_all(graph, clustering)` returns `(produced, notes)`; `cluster(graph)` is what the CLI passes.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_about.py -q`
Expected: FAIL at import (`svarupa.derive.about` does not exist).

- [ ] **Step 3: Write `svarupa/derive/about.py`**

```python
"""One plain sentence per diagram box: what it is, what it does, how healthy.

Every clause is built from a fact the graph holds; a clause with no fact is
left out, never guessed. No AI: the sentences are fixed templates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from svarupa.build import Graph, module_of, modules_shipped
from svarupa.derive.base import DiagramKind, DiagramNode, DiagramSet
from svarupa.health import BY_ID, Health, Violation
from svarupa.health.catalog import SEVERITIES
from svarupa.model import EdgeKind, NodeKind

__all__ = ["About", "about_lines"]

_FRAMEWORKS = {"fastapi": "FastAPI", "flask": "Flask", "express": "Express", "nestjs": "NestJS"}
_STORES = {
    "postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "mongo": "MongoDB",
    "redis": "Redis", "elasticsearch": "Elasticsearch", "clickhouse": "ClickHouse",
    "cassandra": "Cassandra", "influxdb": "InfluxDB", "minio": "MinIO", "sqlite": "SQLite",
    "rabbitmq": "RabbitMQ", "kafka": "Kafka", "nats": "NATS", "activemq": "ActiveMQ",
    "mosquitto": "Mosquitto",
}  # fmt: skip
_CATEGORY = {
    "database": "database", "messagebus": "message bus", "cloud": "cloud service",
    "security": "security service", "frontend": "front-end library",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class About:
    text: str
    worst: str | None = None  # check id of the worst finding, for the passport link


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _and(items: Iterable[str]) -> str:
    xs = list(items)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _module_line(graph: Graph, m: str) -> str:
    arch = graph.architecture_paths
    parts: list[str] = []
    routes = [r for r in graph.routes if r.file in arch and module_of(r.file) == m]
    if routes:
        fw = "/".join(_FRAMEWORKS.get(f, f) for f in sorted({r.framework for r in routes}))
        parts.append(f"serves {_n(len(routes), 'HTTP route')} ({fw})")
    tasks = [t for t in graph.tasks if t.file in arch and module_of(t.file) == m]
    if tasks:
        parts.append(f"runs {_n(len(tasks), 'background task')}")
    if any(e.file in arch and module_of(e.file) == m for e in graph.entrypoints):
        parts.append("starts the program")
    uses = len({d for s, d in graph.module_deps if s == m and d != m})
    used_by = len({s for s, d in graph.module_deps if d == m and s != m})
    if uses or used_by:
        parts.append(f"uses {_n(uses, 'module')} and is used by {used_by}")
    talks = sorted({x.label for x in graph.externals if x.file in arch and module_of(x.file) == m})
    if talks:
        parts.append(f"talks to {_and(talks)}")
    name = m or "."
    return f"Module {name}: {', '.join(parts)}." if parts else f"Module {name}."


def _store_name(image: str) -> str:
    base = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return next((v for k, v in _STORES.items() if base.startswith(k)), base)


def _users(graph: Graph, nid: str) -> list[str]:
    """Compose services that depend on or connect to this one."""
    compose_file, _, name = nid.partition("#service.")
    out: set[str] = set()
    for e in graph.edges:
        if e.kind is EdgeKind.DEPENDS_ON and e.dst == nid and e.src in graph.nodes:
            out.add(graph.nodes[e.src].label)
    for oid, node in graph.nodes.items():
        if oid.startswith(compose_file + "#service.") and name in (node.attr("connects") or "").split("\n"):
            out.add(node.label)
    return sorted(out)


def _compose_line(graph: Graph, nid: str) -> str:
    node = graph.nodes[nid]
    if node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE):
        what = "database" if node.kind is NodeKind.DATASTORE else "queue"
        users = _users(graph, nid)
        used = f", used by {_and(users)}" if users else ""
        return f"{_store_name(node.attr('image') or node.label)} {what} (compose service {node.label}){used}."
    deps = sorted(
        graph.nodes[e.dst].label
        for e in graph.edges
        if e.kind is EdgeKind.DEPENDS_ON and e.src == nid and e.dst in graph.nodes
    )
    built = node.attr("build_context")
    head = f"built from {built}" if built else (f"runs the image {node.attr('image')}" if node.attr("image") else "")
    parts = [p for p in (head, f"depends on {_and(deps)}" if deps else "") if p]
    return f"Service {node.label}: {', '.join(parts)}." if parts else f"Service {node.label}."


def _class_line(graph: Graph, nid: str) -> str:
    node = graph.nodes[nid]
    file = nid.split("#", 1)[0]
    methods = sum(
        1
        for oid, n in graph.nodes.items()
        if n.kind is NodeKind.METHOD and oid.split("#", 1)[0] == file and n.attr("class") == node.label
    )
    bases = sorted(
        graph.nodes[e.dst].label
        for e in graph.edges
        if e.kind is EdgeKind.INHERITS and e.src == nid and e.dst in graph.nodes
    )
    parts = [p for p in (f"with {_n(methods, 'method')}" if methods else "", f"based on {_and(bases)}" if bases else "") if p]
    return f"Class {node.label} {', '.join(parts)}." if parts else f"Class {node.label}."


def _describe(graph: Graph, box: DiagramNode) -> str:
    bid = box.id
    members = box.attr("members")
    if members:
        ms = members.split("\n")
        return f"Group of {_n(len(ms), 'module')}: {_and(ms[:3])}{' and others' if len(ms) > 3 else ''}."
    if bid.startswith("ext:"):
        category = box.attr("external") or bid.split(":", 2)[1]
        mods = {module_of(x.file) for x in graph.externals if x.label == box.label and x.file in graph.architecture_paths}
        return f"External {_CATEGORY.get(category, category)} ({box.label}), used from {_n(len(mods), 'module')}."
    if bid.startswith(("in:", "req:")):
        m = bid.split(":", 1)[1]
        m = "" if m == "." else m
        routes = sorted(
            (r.method, r.path) for r in graph.routes if r.file in graph.architecture_paths and module_of(r.file) == m
        )
        sample = ", ".join(f"{a} {b}" for a, b in routes[:3])
        return f"HTTP entry to {m or '.'}: {_n(len(routes), 'route')}" + (f", for example {sample}." if sample else ".")
    if bid.startswith("env:"):
        return f"Deploy environment {box.label}."
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return _module_line(graph, module)
    node = graph.nodes.get(bid)
    if node is not None:
        if node.kind in (NodeKind.SERVICE, NodeKind.DATASTORE, NodeKind.QUEUE) and "#service." in bid:
            return _compose_line(graph, bid)
        if node.kind is NodeKind.CLASS:
            return _class_line(graph, bid)
    kind = box.kind.replace("_", " ") if box.kind else "Box"
    return f"{kind[:1].upper()}{kind[1:]} {box.label}."


def _findings(graph: Graph, health: Health, box: DiagramNode) -> list[Violation]:
    bid = box.id
    vs = health.violations
    members = box.attr("members")
    if members:
        ms = {"" if m == "." else m for m in members.split("\n")}
        return [v for v in vs if v.module in ms]
    module = "" if bid == "." else bid
    if module in graph.modules and bid not in graph.nodes:
        return [v for v in vs if v.module == module]
    if bid.startswith(("in:", "req:")):
        m = bid.split(":", 1)[1]
        files = {r.file for r in graph.routes if module_of(r.file) == ("" if m == "." else m)}
        return [v for v in vs if v.evidence[0].file in files]
    node = graph.nodes.get(bid)
    if node is not None and node.kind is NodeKind.SERVICE and node.attr("build_context"):
        shipped = modules_shipped(graph, bid)
        if shipped is not None:
            return [v for v in vs if v.module in shipped[0]]
    if node is not None and node.kind in (NodeKind.DATASTORE, NodeKind.QUEUE) and node.evidence:
        line = (node.evidence[0].file, node.evidence[0].start_line)
        return [v for v in vs if any((e.file, e.start_line) == line for e in v.evidence)]
    if node is not None and node.kind in (NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD):
        return [v for v in vs if v.symbol == bid]
    if bid in graph.nodes and "#" not in bid:  # a file box
        return [v for v in vs if v.evidence[0].file == bid]
    files = {e.file for e in box.evidence}
    return [v for v in vs if v.evidence[0].file in files]


def _health_clause(found: list[Violation]) -> tuple[str, str | None]:
    if not found:
        return "No health findings.", None
    worst = min(
        found,
        key=lambda v: (-SEVERITIES.index(BY_ID[v.check].severity), -v.impact, v.check),
    )
    check = BY_ID[worst.check]
    return (
        f"{_n(len(found), 'health finding')}, worst: {check.title} ({check.severity}).",
        worst.check,
    )


def about_lines(
    graph: Graph, health: Health | None, produced: Mapping[DiagramKind, DiagramSet]
) -> dict[str, About]:
    boxes: dict[str, DiagramNode] = {}
    for kind in sorted(produced, key=lambda k: k.value):
        for spec_id in sorted(produced[kind].specs):
            for box in produced[kind].specs[spec_id].nodes:
                boxes.setdefault(box.id, box)
    out: dict[str, About] = {}
    for bid in sorted(boxes):
        text = _describe(graph, boxes[bid])
        if health is None:
            out[bid] = About(text)
            continue
        clause, worst = _health_clause(_findings(graph, health, boxes[bid]))
        out[bid] = About(f"{text} {clause}", worst)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_about.py -q`
Expected: PASS. If an assertion fails because a fact is spelled differently in the graph (for example `RouteFact.method` lower case, or the external label is "SQL database" for psycopg2), read the real value from the graph and fix the template or the expectation to match the fact, then ledger it. Never invent a clause the graph has no fact for.

- [ ] **Step 5: Lint, type check, commit**

```bash
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa/derive/about.py tests/test_about.py
git commit -m "Describe every diagram box in one plain sentence"
```

---

### Task 6: Box lines in graph.json, get_node and the passport

**Files:**
- Modify: `svarupa/emit/data.py` (`graph_json` gains `about`)
- Modify: `svarupa/emit/__init__.py` (compute once, pass to `graph_json` and `render_viewer`)
- Modify: `svarupa/emit/viewer.py` (embedded data, passport element, script)
- Modify: `svarupa/query/__init__.py` (`get_node`)
- Test: `tests/test_about.py`, `tests/js/viewer_harness.js`

**Interfaces:**
- Consumes: `about_lines`, `About` (Task 5).
- Produces: `graph.json["about"]: {box id: text}`; `get_node` result key `about`; `<script type="application/json" id="about-data">` with `{box id: [text, worst or null]}`; passport element `#panel-about`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_about.py`)

```python
import json  # noqa: E402

from svarupa.cli import main  # noqa: E402


def test_about_reaches_graph_json_get_node_and_the_page(tmp_path: Path, capsys) -> None:
    api_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    capsys.readouterr()
    gj = json.loads((tmp_path / ".svarupa" / "graph.json").read_text(encoding="utf8"))
    assert gj["about"]["api"].startswith("Module api: serves 2 HTTP routes")
    assert main(["query", str(tmp_path / ".svarupa"), "get_node", "api", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["about"].startswith("Module api:")
    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    assert '<script type="application/json" id="about-data">' in html
    assert 'id="panel-about"' in html


def test_about_data_is_escaped_in_the_page(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  \"x</script><b>\":\n    image: redis\n",
    )
    api_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    html = (tmp_path / ".svarupa" / "index.html").read_text(encoding="utf8")
    start = html.index('id="about-data">')
    blob = html[start : html.index("</script>", start)]
    assert "<" not in blob.split(">", 1)[1]
```

In `tests/js/viewer_harness.js`, after the line `check('the id chip still carries the id', ...)`, add:

```javascript
check('the passport shows the box line', (document.getElementById('panel-about').textContent || '').length > 0, document.getElementById('panel-about').textContent);
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_about.py tests/test_viewer_js.py -q`
Expected: FAIL (`KeyError: 'about'`; harness reports `FAIL the passport shows the box line` or a missing element).

- [ ] **Step 3: graph.json** (`svarupa/emit/data.py`)

Add a parameter `about: Mapping[str, str] | None = None` to `graph_json` (import `Mapping` from `collections.abc`), and before `return out`:

```python
    if about is not None:
        out["about"] = dict(sorted(about.items()))
```

- [ ] **Step 4: Compute once** (`svarupa/emit/__init__.py`)

Add `from svarupa.derive.about import about_lines`. Before `gj = graph_json(...)`:

```python
    about = about_lines(graph, health, produced)
```

Change the call to `graph_json(graph, rationale, head, dirty, health, design, {k: a.text for k, a in about.items()})` and the `render_viewer(...)` call to pass `about=about` as a keyword argument.

- [ ] **Step 5: The page and the passport** (`svarupa/emit/viewer.py`)

Add `about: Mapping[str, About] | None = None` to `render_viewer` (import `About` from `svarupa.derive.about`, `Mapping` from `collections.abc`, `json` at the top). Add the helper:

```python
def _about_data(about: Mapping[str, About] | None) -> Markup:
    """Box lines for the passport, embedded once. `<`, `>` and `&` are
    escaped inside the JSON so repository text can never end the script."""
    data = {k: [a.text, a.worst] for k, a in sorted((about or {}).items())}
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return raw(f'<script type="application/json" id="about-data">{text}</script>')
```

Put `_about_data(about)` into `body`'s `join((...))` right after `_health_tab(health, design, graph)`. In the passport `aside`, right after `tag("h2", raw(""), id="panel-title"),` add:

```python
                        tag("p", raw(""), id="panel-about", class_="about"),
                        raw('<a id="panel-why" class="why-link" href="#d-health" hidden>why?</a>'),
```

In the script, after `var sumEl = ...`, add:

```javascript
  var aboutEl = document.getElementById('panel-about');
  var whyEl = document.getElementById('panel-why');
  var ABOUT = {};
  try { ABOUT = JSON.parse(document.getElementById('about-data').textContent || '{}'); } catch (e) { ABOUT = {}; }
```

In `function passport(node)`, after `subEl.textContent = ...`, add:

```javascript
    var line = isFrame ? null : ABOUT[id];
    aboutEl.textContent = line ? line[0] : '';
    whyEl.hidden = !(line && line[1]);
    if (line && line[1]) whyEl.setAttribute('data-why', line[1]);
    else whyEl.removeAttribute('data-why');
```

Add CSS: `.panel .about { margin: .2rem 0 .6rem; }` next to the other passport rules (use the selector style the passport rules already use).

- [ ] **Step 6: get_node** (`svarupa/query/__init__.py`)

In `get_node`, build the result into a variable and add the line when present:

```python
    out = {
        "query": label,
        "matched_by": by,
        "match": {**_brief(n), "lang": n.get("lang"), "attrs": n.get("attrs", {})},
        "out_degree": len(index.out.get(nid, [])),
        "in_degree": len(index.inc.get(nid, [])),
    }
    about = index.data.get("about")
    if isinstance(about, dict) and nid in about:
        out["about"] = about[nid]
    return out
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_about.py tests/test_viewer_js.py tests/test_emit.py tests/test_graph_query.py -q`
Expected: PASS (the viewer JS test runs when node and jsdom are installed; they are under `tests/js/node_modules`).

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -q -x 2>&1 | tail -3
uv run ruff format svarupa tests && uv run ruff check svarupa tests && uv run pyright svarupa
git add svarupa tests
git commit -m "Show each box's plain line in the passport, graph.json and get_node"
```

Expected: suite PASS. If byte-identity or golden tests change only because graph.json gained `about` and `checks[].explanation`, confirm the new keys are the only diff, regenerate, and ledger a ruling.

---

### Task 7: Mutations and docs

**Files:**
- Modify: `scripts/mutate_packs.py`
- Modify: `docs/health.md`

- [ ] **Step 1: Add the mutations** (append to `MUTATIONS`; add `"tests/test_explain.py"` and `"tests/test_about.py"` to `SUITE`; add the promises to the docstring list)

```python
    (
        "a missing required example passes",
        "tests/test_explain.py",
        "        if example_for(c.id, lang) is None\n",
        "        if False\n",
    ),
    (
        "javascript loses the typescript example",
        "svarupa/health/explain.py",
        '_SAME_AS = {"javascript": "typescript"}\n',
        "_SAME_AS: dict[str, str] = {}\n",
    ),
    (
        "a box takes every finding",
        "svarupa/derive/about.py",
        "        return [v for v in vs if v.module == module]\n",
        "        return list(vs)\n",
    ),
```

Docstring lines:

```
* every language that can fire has an example;
* a JavaScript finding shows the TypeScript example;
* a box counts only its own findings.
```

Note: the first entry mutates a test, not the product. If the mutation runner only restores product files, change its path to `svarupa/health/explain.py` and its target to the `out.add(lang)` line under `_FUNCTION_CHECKS` (so the required set shrinks and `test_required_languages_follow_the_packs` goes red), and ledger the change.

- [ ] **Step 2: Run the mutation check**

Run: `uv run python scripts/mutate_packs.py 2>&1 | tail -4`
Expected: every mutation caught (42/42).

- [ ] **Step 3: Update `docs/health.md`**

After the Ratings section, add:

```markdown
## Explanations

Every check has a plain explanation: what it means, why it matters and how
to fix it, with a short before/after in Python, TypeScript/JavaScript, Go
or Java where the fix is code. They live in `svarupa/health/explain/` and
show in REPORT.md ("What these mean"), the Health tab ("why?"), graph.json
(`health.checks[].explanation`) and MCP `get_health`. From the command line:

    svarupa explain                      # list the checks
    svarupa explain query-in-loop        # one check, every example
    svarupa explain query-in-loop --lang go

Every box on the map also has one plain sentence in its passport, in
graph.json (`about`) and in `get_node`, for example "Module app/api: serves
12 HTTP routes (FastAPI), uses 3 modules and is used by 5, talks to
PostgreSQL. 2 health findings, worst: Query in a loop (major)."
```

- [ ] **Step 4: Commit**

```bash
git add scripts/mutate_packs.py docs/health.md
git commit -m "Cover explanations with mutations and document them"
```
