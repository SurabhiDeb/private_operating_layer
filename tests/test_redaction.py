"""Masking personal data before it is stored. PRD B6, AC-25, SEC-8.

Two tests that look similar and are doing opposite jobs.

The corpus test proves the redactor works, against text written to contain each shape,
with the forbidden substrings listed by hand so the assertion does not share its
patterns with the code under test.

The fixture-corpora test proves something much weaker on purpose, and says so: that the
reference products' inputs contain no PII today. It is a regression guard on the
fixtures, not evidence about `redact`, because a clean corpus passes whether the
redactor runs or not. Keeping the two apart is the point — AC-25 as written ("proven by
test over the fixture corpora") would otherwise be satisfiable by a function that
returns its argument.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer.core.redact import PATTERNS, find_pii, redact

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = json.loads((FIXTURES / "pii_corpus.json").read_text())


def _corpus_cases():
    return [(case["id"], case["input"], case["must_not_survive"]) for case in CORPUS["cases"]]


@pytest.mark.parametrize("case_id,text,forbidden", _corpus_cases())
def test_no_listed_string_survives_redaction(case_id, text, forbidden):
    """The non-circular half: hand-written literals, not regex findings."""
    masked = redact(text)
    for secret in forbidden:
        assert secret not in masked, f"{case_id}: {secret!r} survived as {masked!r}"


def test_the_corpus_covers_every_pattern_the_module_claims():
    """A pattern with no case in the corpus is a pattern nobody has tested. This fails
    when someone adds a shape to `PATTERNS` and no example alongside it."""
    exercised = {
        kind
        for case in CORPUS["cases"]
        for kind, _ in find_pii(case["input"])
    }
    declared = {name for name, _ in PATTERNS}
    assert declared - exercised == set(), f"untested patterns: {sorted(declared - exercised)}"


def test_redaction_labels_what_it_removed_rather_than_blanking_it():
    """`[email]` rather than a row of asterisks. A human triaging a failing case needs
    to know that an email was there to understand the input at all, and a uniform mask
    turns three different inputs into the same unreadable string."""
    masked = redact("write to jo@example.com or ring 07700 900123")
    assert "[email]" in masked and "[phone]" in masked


def test_text_with_nothing_sensitive_is_returned_unchanged():
    """Over-redaction is also a defect: it makes the stored evidence useless."""
    plain = "The bot told me to go to a branch when I asked about a refund."
    assert redact(plain) == plain


def test_none_stays_none():
    """A case with no recorded input is a fact, not an empty string. The third fixture's
    per-case rows carry no input field at all."""
    assert redact(None) is None
    assert find_pii(None) == []


def test_a_tenants_own_pattern_is_applied():
    """`extra_patterns` exists because no fixed list covers what another company's
    domain leaks — a policy number, a customer reference, an internal id."""
    masked = redact("policy ABC-99-12345 lapsed", extra_patterns=[("policy", r"ABC-\d\d-\d+")])
    assert "ABC-99-12345" not in masked and "[policy]" in masked


def test_a_pattern_that_does_not_compile_is_refused_not_ignored():
    """It arrives from tenant config. A pattern that silently fails to compile is a
    pattern nobody is masking with, which is the quiet version of the failure B6 gives
    no tolerance for."""
    with pytest.raises(ValueError) as caught:
        redact("anything", extra_patterns=[("broken", r"([unclosed")])
    assert "does not compile" in str(caught.value)


def test_the_limit_is_documented_rather_than_claimed():
    """The corpus's last case. A name is personal data and no pattern finds it, so the
    design does not rest on redaction: an input is stored only for a case that did not
    pass, and the untouched conversation stays at the source behind `trace_url`."""
    limit = next(c for c in CORPUS["cases"] if c["id"] == "pii-7-the-limit")
    assert limit["must_not_survive"] == []
    assert "Daniel" in redact(limit["input"]), (
        "if a name is now masked, the corpus note is out of date rather than the test"
    )
