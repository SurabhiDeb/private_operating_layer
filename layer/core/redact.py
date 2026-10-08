"""Masking personal data before it is stored, never after.

`case_result.input_redacted` holds the input of a case that did not pass, so that a
human reading a finding can see what the product was asked. That text is somebody
else's customer talking to their product, and PRD B6 sets unredacted PII in stored case
inputs at **zero tolerance** — it is one of only two metrics in the document with no
tolerance band at all. The fixtures' own `shared/redact.py` has the same instinct and
the handoff says to port it; this is a reimplementation rather than an import, for the
same reason `layer/verdicts/stats.py` reimplements the Wilson interval: a fixture is
test data and may never become a dependency.

**Where this runs.** At extraction, in the metric engine, not at the database. The type
that carries the text is called `input_redacted` and there is no code path that puts raw
input into it, so raw customer text never reaches a candidate object, a log line or a
traceback. Redacting at the last moment before the INSERT would leave the raw string
sitting in memory across the whole import, where any exception could print it.

**What it does not claim.** This finds shapes — an address-shaped string, a card-shaped
digit run — and it cannot find a name. "My husband passed away last month" is personal
data by any reading and no regular expression will mask it. So the design does not rest
on redaction alone: an input is stored only for a case that did not pass, which removes
roughly 90% of it, and `extra_patterns` lets a tenant add what their own domain leaks.
A reader who needs the untouched conversation follows `trace_url` to the source, where
it is governed by that platform's retention and access rules rather than by this file.

**Why the replacement is labelled.** `[email]` rather than `[redacted]` or a row of
asterisks, because a human triaging a failing case needs to know *that an email was
there* to understand the input at all. A uniform mask turns three different inputs into
the same unreadable string.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = ["redact", "find_pii", "PATTERNS"]

#: Ordered, and the order is load-bearing: a 16 digit card must be masked before the
#: generic long-digit rule can take the first eleven of it for a phone number, and a
#: sort code must go before the bare-account rule can see its halves.
PATTERNS: tuple[tuple[str, str], ...] = (
    ("email", r"[\w.+-]+@[\w-]+\.[\w.-]*[\w]"),
    # Before `card`, which would otherwise take the digit groups out of the middle of
    # an IBAN and leave its country code and bank code standing in the clear. Found by
    # running the module over an example rather than by reading it.
    ("iban", r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
    # Card-shaped: 13 to 19 digits, optionally spaced or hyphenated in groups.
    ("card", r"\b(?:\d[ -]?){12,18}\d\b"),
    # Before `sort_code`, because a National Insurance number written with spaces —
    # `JG 12 34 56 A` — contains a sort-code-shaped run in its middle, and masking that
    # run first leaves the letters standing either side of it.
    #
    # Deliberately looser than the real National Insurance format, which excludes
    # several prefix letters. A redactor is not a validator: masking a string that only
    # looks like an NI number costs a reader nothing, and declining to mask a real one
    # because its prefix was unusual is the failure B6 gives no tolerance for.
    ("ni_number", r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]?\b"),
    ("sort_code", r"\b\d{2}[- ]\d{2}[- ]\d{2}\b"),
    ("phone", r"(?:\+\d{1,3}[\s-]?)?\b0?\d{3,4}[\s-]?\d{3}[\s-]?\d{3,4}\b"),
    ("postcode", r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"),
    ("account_number", r"\b\d{8}\b"),
    ("ip", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
)

_COMPILED = tuple((name, re.compile(pattern)) for name, pattern in PATTERNS)


def _compile_extra(extra: Iterable[tuple[str, str]] | None):
    if not extra:
        return ()
    out = []
    for name, pattern in extra:
        try:
            out.append((name, re.compile(pattern)))
        except re.error as exc:
            # A tenant's own config, so it is refused rather than ignored: a pattern
            # that silently fails to compile is a pattern nobody is masking with.
            raise ValueError(f"redaction pattern {name!r} does not compile: {exc}") from exc
    return tuple(out)


def redact(text: str | None, *, extra_patterns: Iterable[tuple[str, str]] | None = None) -> str | None:
    """Mask every known shape in `text`, labelling what was removed.

    None in, None out: a case with no recorded input is a fact, not an empty string.
    """
    if text is None:
        return None
    out = text
    for name, pattern in (*_COMPILED, *_compile_extra(extra_patterns)):
        out = pattern.sub(f"[{name}]", out)
    return out


def find_pii(
    text: str | None, *, extra_patterns: Iterable[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    """Every match, as (kind, matched text). For the tests and for a tenant audit.

    Note what this can and cannot be evidence of. Run over the Layer's stored inputs it
    proves only that these shapes are gone, and it shares its patterns with `redact`,
    so on its own it is circular. The non-circular half is a corpus written to contain
    each shape, which is `tests/fixtures/pii_corpus.json`.
    """
    if text is None:
        return []
    found: list[tuple[str, str]] = []
    for name, pattern in (*_COMPILED, *_compile_extra(extra_patterns)):
        for match in pattern.findall(text):
            found.append((name, match if isinstance(match, str) else match[0]))
    return found
