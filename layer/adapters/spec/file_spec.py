"""Reading a specification document into clause candidates.

Deterministic, with no model call. That is a decision rather than a limitation: the
structural pass handles headings, tables, list items and stated targets, which covers the
documents seen so far, and it is reproducible, free, and testable without an API key.
An LLM pass belongs on top of this as an *enhancement* for prose a parser cannot reach —
never underneath it, because then every import depends on a provider being up and on two
runs agreeing (EC-10 requires failing closed, which a non-deterministic importer makes
hard to reason about).

**Where product-specific knowledge is allowed to live.** In `source.config`, never here.
A spec source may say which columns carry the metric, the target and the rationale, and
how its section titles map to clause kinds. The defaults below are generic English — the
words "metric", "budget", "non-goal", "contract" — and they are overridable precisely
because a stranger's spec will not use them. Fixture C exists to prove that path works,
since a parser tuned to two documents by one author would look agnostic and not be.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from layer.adapters.base import ClauseCandidate, Failed, ImportReport, Skipped
from layer.adapters.parse import markdown as md
from layer.adapters.spec.values import normalise_metric_name, parse_target

#: Column headers that mean "the name of the thing being promised".
METRIC_COLUMNS = ("metric", "", "name", "measure", "item", "#")
#: Column headers that mean "the number it must reach".
TARGET_COLUMNS = ("target", "bar", "threshold", "value", "goal", "budget", "limit")
#: Column headers that mean "why that number".
RATIONALE_COLUMNS = ("why", "why this number", "rationale", "reason", "notes", "note")
#: Column headers that carry the promise itself when there is no metric/target split.
STATEMENT_COLUMNS = ("failure", "rule", "situation", "case", "requirement", "description")

#: Generic section vocabulary to clause kind. Overridable per source; a title that matches
#: nothing becomes a `rule`, which PRD H6 requires to be held and never counted as drift.
SECTION_KINDS: tuple[tuple[str, str], ...] = (
    (r"success\s+metric|^metrics?$|measurement", "threshold"),
    (r"budget|cost|latency|performance", "threshold"),
    (r"output\s+contract|contract|schema|response\s+format", "contract"),
    (r"non.?goal|out\s+of\s+scope|will\s+not|does\s+not", "non_goal"),
    (r"known\s+hard|hard\s+case|edge\s+case", "hard_case"),
    (r"unacceptable|must\s+not|never|forbidden|failure", "rule"),
    (r"polic|rule|ambiguit|when\s+it", "rule"),
)


@dataclass(frozen=True)
class SpecConfig:
    """The shape of one spec source. Everything product-specific is here."""

    path: str
    ref_prefix: str = "C"
    metric_columns: tuple[str, ...] = METRIC_COLUMNS
    target_columns: tuple[str, ...] = TARGET_COLUMNS
    rationale_columns: tuple[str, ...] = RATIONALE_COLUMNS
    statement_columns: tuple[str, ...] = STATEMENT_COLUMNS
    section_kinds: tuple[tuple[str, str], ...] = SECTION_KINDS
    #: Sections to ignore entirely, by title pattern. For appendices and changelogs.
    ignore_sections: tuple[str, ...] = (r"^appendix", r"revision|changelog|where this came from")

    @classmethod
    def from_dict(cls, config: dict) -> SpecConfig:
        kinds = config.get("section_kinds")
        return cls(
            path=config["path"],
            ref_prefix=config.get("ref_prefix", "C"),
            metric_columns=tuple(config.get("metric_columns", METRIC_COLUMNS)),
            target_columns=tuple(config.get("target_columns", TARGET_COLUMNS)),
            rationale_columns=tuple(config.get("rationale_columns", RATIONALE_COLUMNS)),
            statement_columns=tuple(config.get("statement_columns", STATEMENT_COLUMNS)),
            section_kinds=tuple(tuple(k) for k in kinds) if kinds else SECTION_KINDS,
            ignore_sections=tuple(config.get("ignore_sections", cls.ignore_sections)),
        )


class SpecAdapter:
    """`role = "spec"`. Markdown in, clause candidates out."""

    role = "spec"

    def read(self, document: str | bytes, config: dict) -> ImportReport:
        spec = SpecConfig.from_dict(config)
        text = document.decode("utf-8", "replace") if isinstance(document, bytes) else document
        report = ImportReport(source_role=self.role)

        sections = md.parse(text)
        report.enumerated = len(sections)
        # Shared across sections: two unnumbered subsections inheriting one parent ref
        # must not both start their ordinals at 1 and produce the same ref twice.
        ordinals: dict[str, int] = {}

        for section in sections:
            title = section.title or "(preamble)"
            if self._ignored(section, spec):
                report.skipped.append(Skipped(title, "section_ignored"))
                continue
            try:
                found = self._from_section(section, spec, ordinals)
            except Exception as exc:
                # EC-2: name the part that failed and import the rest. One unparseable
                # section must not cost the other twenty.
                report.failed.append(
                    Failed(title, "section_unparsed", f"{type(exc).__name__}: {exc}")
                )
                continue
            if found:
                report.candidates.extend(found)
                report.imported += 1
            else:
                report.skipped.append(Skipped(title, "no_clauses_found"))

        if report.failed:
            report.notes.append(
                f"{len(report.failed)} section(s) did not parse and were not imported: "
                + ", ".join(f.identifier for f in report.failed)
            )
        return report

    # -- sections ----------------------------------------------------------------

    def _ignored(self, section: md.Section, spec: SpecConfig) -> bool:
        title = (section.title or "").lower()
        return any(re.search(p, title) for p in spec.ignore_sections)

    def _kind_of(self, section: md.Section, spec: SpecConfig) -> str:
        title = (section.title or "").lower()
        for pattern, kind in spec.section_kinds:
            if re.search(pattern, title):
                return kind
        return "rule"

    def _from_section(
        self, section: md.Section, spec: SpecConfig, ordinals: dict[str, int]
    ) -> list[ClauseCandidate]:
        kind = self._kind_of(section, spec)
        out: list[ClauseCandidate] = []
        key = section.ref or (section.title or "x").lower()

        def next_ordinal() -> int:
            ordinals[key] = ordinals.get(key, 0) + 1
            return ordinals[key]

        for table in section.tables:
            for row in table.rows:
                ordinal = next_ordinal()
                candidate = self._from_row(section, table, row, spec, kind, ordinal)
                if candidate:
                    out.append(candidate)
                else:
                    ordinals[key] -= 1

        # Only mine prose for a section that produced no table rows. A section with a
        # metrics table also has paragraphs explaining it, and importing those as clauses
        # would double-count every promise it makes.
        if not out:
            for line_no, item in section.list_items():
                out.append(
                    self._from_text(section, item, spec, kind, next_ordinal(), line_no)
                )
            if not out:
                for line_no, line in section.paragraph_lines():
                    # Driven by what the sentence says, not by what the heading is
                    # called. An earlier version mined prose only in sections whose
                    # title matched a vocabulary of English words, so a document with a
                    # heading like "Service levels" had every one of its bars ignored —
                    # which is precisely the fitting that AC-21 exists to catch.
                    for part in _clauses_in(line):
                        if _states_an_obligation(part):
                            out.append(
                                self._from_text(
                                    section, part, spec, kind, next_ordinal(), line_no
                                )
                            )
        return out

    # -- rows and lines ----------------------------------------------------------

    def _from_row(self, section, table, row, spec, kind, ordinal) -> ClauseCandidate | None:
        label = row.get(*spec.metric_columns).strip()
        if not label and table.headers:
            # No configured metric column matched. In a table of promises the first
            # column is the subject, so fall back to it rather than discarding the row:
            # requiring config for every possible header spelling would make an
            # unfamiliar document fail for a cosmetic reason.
            label = row.cells.get(table.headers[0], "").strip()
        target_text = row.get(*spec.target_columns).strip()
        statement_text = row.get(*spec.statement_columns).strip()
        rationale = row.get(*spec.rationale_columns).strip() or None

        # A table with neither a target nor a statement column is reference material —
        # a glossary, a list of teams — not a set of promises.
        if not target_text and not statement_text:
            return None
        if not label and not statement_text:
            return None

        target = parse_target(target_text) if target_text else None
        if target_text and target is None and not statement_text:
            # The column exists and holds something unreadable. Not a silent drop: the
            # clause is still imported, as a rule with no bar, so the promise survives
            # even though its number did not.
            kind = "rule" if kind == "threshold" else kind

        statement = statement_text or (
            f"{label} {target_text}".strip() if target_text else label
        )
        metric = normalise_metric_name(label) if label and target else None

        return ClauseCandidate(
            identity_key=self._identity(section, metric, label or statement),
            ref_hint=self._ref(spec, section, ordinal),
            kind="threshold" if target else kind,
            section=section.ref,
            label=label or None,
            statement=_sentence(statement),
            rationale=rationale,
            metric=metric,
            comparator=target.comparator if target else None,
            value=target.value if target else None,
            value_high=target.value_high if target else None,
            unit=target.unit if target else None,
            direction=target.direction if target else None,
            k=_k_in(label) or _k_in(statement),
            source_locator=f"L{row.line}",
            assumptions=target.assumptions if target else (),
        )

    def _from_text(self, section, text, spec, kind, ordinal, line_no) -> ClauseCandidate:
        # A bar is recognised wherever it is stated, not only under a heading whose
        # wording the parser happens to know. The obligation guard is what keeps a
        # hard-case description that merely mentions a number from becoming a promise.
        wants_target = kind == "threshold" or _states_an_obligation(text)
        target = parse_target(text) if wants_target else None
        label = _label_of(text)
        # Deliberately no metric name. A table column names the measure; a sentence only
        # implies one, and a slug built from prose — `on_no_less_than_95_of_unusable_photos`
        # — would never match what the eval source calls the number. The clause records
        # that a bar exists and a human binds the metric at onboarding step 5, which is
        # what `binding` is for.
        assumptions = target.assumptions if target else ()
        if target:
            assumptions += (
                "metric not named in the source: read from prose, so a human must bind "
                "the measurement to this clause",
            )
        return ClauseCandidate(
            identity_key=self._identity(section, None, text),
            ref_hint=self._ref(spec, section, ordinal),
            kind="threshold" if target else kind,
            section=section.ref,
            label=label,
            statement=_sentence(text),
            metric=None,
            comparator=target.comparator if target else None,
            value=target.value if target else None,
            value_high=target.value_high if target else None,
            unit=target.unit if target else None,
            direction=target.direction if target else None,
            k=_k_in(text),
            source_locator=f"L{line_no}",
            assumptions=assumptions,
        )

    # -- identity ----------------------------------------------------------------

    def _identity(self, section: md.Section, metric: str | None, fallback: str) -> str:
        """What survives a rewrite.

        The metric name where there is one, because that is what the eval source will
        also call it, and it is far more stable than a sentence. Otherwise the section
        plus a hash of the normalised text — still stable under punctuation and casing
        changes, though not under a full rewrite, which is the honest limit of a
        deterministic importer and the reason step 7 also matches on similarity.
        """
        base = section.ref or (section.title or "").lower()[:24]
        if metric:
            return f"{base}:{metric}"
        normalised = re.sub(r"[^a-z0-9]+", " ", fallback.lower()).strip()
        digest = hashlib.sha256(normalised.encode()).hexdigest()[:10]
        return f"{base}:{digest}"

    def _ref(self, spec: SpecConfig, section: md.Section, ordinal: int) -> str:
        section_part = section.ref or re.sub(
            r"[^a-z0-9]+", "", (section.title or "x").lower()
        )[:6]
        return f"{spec.ref_prefix}-{section_part}.{ordinal}"


# -- helpers --------------------------------------------------------------------

_K = re.compile(r"@\s*(\d+)")


def _k_in(text: str | None) -> int | None:
    """`recall@5` carries its own k, which the threshold arithmetic needs."""
    if not text:
        return None
    match = _K.search(text)
    return int(match.group(1)) if match else None


def _label_of(text: str) -> str:
    """The leading noun phrase of a sentence, for a human-readable label."""
    first = re.split(
        r"[.:;]|\s+(?:must|shall|should|is|are|at\s+least|under|no\s+more|wants?|remains?)\b",
        text,
        maxsplit=1,
    )[0]
    return " ".join(first.split())[:120] or text[:120]


def _sentence(text: str) -> str:
    text = " ".join(str(text).split())
    if text and text[-1] not in ".!?":
        text += "."
    return text


#: Words that mark a sentence as stating a requirement rather than describing something.
#: Generic English, and the reason prose mining does not depend on a heading's wording.
_OBLIGATION = re.compile(
    r"\b(?:must|shall|should|will|needs?\s+to|required|has\s+to|have\s+to|"
    r"at\s+least|at\s+most|no\s+less|no\s+more|under|below|over|above|between|"
    r"within|remain|remains|stay|stays|keep|keeps|wants?\s+to|target|budget)\b",
    re.IGNORECASE,
)


def _states_an_obligation(text: str) -> bool:
    """A line is a threshold only if it both names a number and asks for something.

    Without the second half, a sentence like "volume is roughly 4,000 messages per day"
    becomes a promise the product never made.
    """
    return bool(_OBLIGATION.search(text)) and parse_target(text) is not None


def _clauses_in(line: str) -> list[str]:
    """Split a sentence that carries more than one bar.

    "Median round trip should stay under 1500ms, and the 95th percentile under 4
    seconds" states two thresholds. Taking the first and discarding the second would
    lose a promise silently, which is the failure mode this whole adapter is arranged
    against.
    """
    parts = re.split(r";|,\s+(?:and|with|while)\s+", line)
    return [p.strip() for p in parts if p.strip()] or [line]
