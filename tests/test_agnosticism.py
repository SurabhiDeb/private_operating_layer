"""The agnosticism contract, enforced rather than remembered. R1, R2, R3, Appendix F.

PRD Appendix F: the fixtures "are named nowhere in Parts A or B, and nothing in the
implementation may reference them". CLAUDE.md puts it as a defect rather than a
trade-off: a fixture name in a migration, a seed script, a default config or a pattern
definition is a defect, and `tests/` is the only place a real product name may appear.

**This file exists because the violations were real.** When it was first written, ten
references to the reference products sat in `layer/` — in docstrings, a glob example, and
one in a user-facing error message that would have told a tenant with no such product to
go and look at another company's clause ref. Two of them had been added that same week,
in steps 11 and 12, by someone who had read the rule. A rule nothing checks is a
preference.

**Why it greps the source rather than inspecting imports.** R1 is about every string a
reader or a caller can see — a comment, a default, an error message — not only about code
paths that execute. An import graph would have found none of the ten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LAYER = Path(__file__).resolve().parent.parent / "layer"
TESTS = Path(__file__).resolve().parent

#: The fixture products, their metrics and their ref prefixes. The one list in the suite
#: that is allowed to name them, because its job is to find them elsewhere.
#:
#: Written as word-boundary patterns on purpose: "triage" is a product key here and
#: "triaging" is an ordinary English verb that appears in several docstrings about
#: reading a failing case. A substring match would fail on the verb and teach whoever
#: hits it to weaken the test.
FIXTURE_NAMES = (
    r"triage",
    r"policydesk",
    r"wayfinder",
    r"chatbot-lab",
    r"escalation_recall",
    r"team_accuracy",
    r"contract_validity",
    r"status_ok",
    r"critical_pass_rate",
    r"groundedness",
    r"suggestion_correct",
    r"accepted_suggestions",
    r"p95_round_trip",
    r"needs_clarification",
    r"TRI-\d",
    r"PD-\d",
    r"WF-\d",
    r"NIM-\d",
)

#: Every Python file the Layer ships. `__pycache__` is excluded by the glob.
SOURCES = sorted(p for p in LAYER.rglob("*.py") if "__pycache__" not in p.parts)

#: Migrations ship too, and a fixture name in one is the specific defect Appendix F
#: names. They are inside `layer/` and so already covered, but listed here so a reader
#: can see the claim is not limited to application code.
MIGRATIONS = sorted((LAYER / "db" / "alembic" / "versions").glob("*.py"))


def test_the_suite_ships_source_to_check():
    """A guard on the guard. If `SOURCES` were empty — a moved package, a changed
    layout — every test below would pass by having nothing to look at."""
    assert len(SOURCES) > 25, len(SOURCES)
    assert MIGRATIONS, "no migrations found, so the claim about them proves nothing"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_no_fixture_name_appears_in_the_implementation(name):
    """R1 and Appendix F, over every line of every file the Layer ships."""
    pattern = re.compile(rf"\b{name}\b", re.IGNORECASE)
    offenders = []
    for path in SOURCES:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(LAYER.parent)}:{number}: {line.strip()[:80]}")
    assert offenders == [], (
        f"the fixture name {name!r} appears in the implementation:\n"
        + "\n".join(offenders)
    )


def test_no_adapter_branches_on_a_product_key():
    """R2: "Shape in config, mechanism in the adapter, never a branch on a product key".

    The prototype's `workflows.py:53` was exactly `if product.key == ...`, and it is why
    the rule is written down. Any comparison of a product's key or pattern to a literal
    is the same mistake wearing different spelling.
    """
    patterns = (
        re.compile(r"product(?:\.\w+)*\.key\s*==\s*['\"]"),
        re.compile(r"product(?:\.\w+)*\.pattern\s*==\s*['\"]"),
        re.compile(r"['\"]\w+['\"]\s*==\s*product(?:\.\w+)*\.(?:key|pattern)"),
        re.compile(r"\.key\s+in\s*\(['\"]"),
    )
    offenders = []
    for path in SOURCES:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if any(p.search(line) for p in patterns):
                offenders.append(f"{path.relative_to(LAYER.parent)}:{number}: {line.strip()}")
    assert offenders == [], "an adapter branches on which product it is reading:\n" + "\n".join(offenders)


def test_the_open_vocabularies_are_not_postgres_enums():
    """R3: `pattern`, `source.kind`, `clause.kind` and `link_type` are validated in
    Python against a registry, so adding a source system or a clause kind needs no
    migration. A `CREATE TYPE ... AS ENUM` for one of them would make it a schema change.
    """
    sql = "\n".join(p.read_text() for p in MIGRATIONS).lower()
    assert "as enum" not in sql, "a Postgres enum was created; R3 forbids it for the open sets"
    for column in ("pattern", "link_type"):
        assert f"check ({column} in" not in sql, (
            f"{column} carries a CHECK constraint, which closes a vocabulary R3 "
            f"requires to stay open"
        )


def test_the_closed_sets_do_carry_a_check():
    """The other half of R3, and the reason the rule is not "never constrain anything".

    `product.status`, `source.role`, `state`, `verdict` and `source_kind` are sets the
    Layer branches on, so they get a CHECK. A test that only forbade constraints would
    be satisfied by a schema with none at all.
    """
    sql = "\n".join(p.read_text() for p in MIGRATIONS).lower()
    for column in ("status", "state", "verdict", "outcome", "scope"):
        assert f"{column} in (" in sql, f"no CHECK found for the closed set {column}"


def test_no_default_config_names_a_product():
    """Appendix F's specific list: a fixture name in a default config or a pattern
    definition. Checked by looking at what the defaults actually contain rather than at
    where they live, since a default can be written in any module."""
    suspicious = re.compile(
        r"(?:default|DEFAULTS?|PATTERNS?|SEED)\w*\s*[:=].*"
        r"(?:" + "|".join(FIXTURE_NAMES) + r")",
        re.IGNORECASE,
    )
    offenders = [
        f"{path.relative_to(LAYER.parent)}:{number}"
        for path in SOURCES
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if suspicious.search(line)
    ]
    assert offenders == [], offenders


def test_the_fixture_repository_is_never_imported():
    """It is read-only test data and never a dependency (CLAUDE.md). A path to it inside
    `layer/` would make the Layer unable to run on a machine that does not have it."""
    offenders = []
    for path in SOURCES:
        text = path.read_text()
        for marker in ("chatbot-lab", "Desktop/chatbot"):
            if marker in text:
                offenders.append(f"{path.relative_to(LAYER.parent)}: {marker}")
    assert offenders == [], offenders


def test_the_tests_are_where_the_names_live():
    """The converse, and it is not decoration: if no test named a fixture, the suite
    would be testing the Layer against nothing real, and every assertion above would be
    trivially true."""
    named = [
        p.name for p in TESTS.glob("test_*.py")
        if re.search(r"\b(triage|policydesk|wayfinder)\b", p.read_text(), re.IGNORECASE)
    ]
    assert len(named) >= 4, f"only {named} name a reference product"
