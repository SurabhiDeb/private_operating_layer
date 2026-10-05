"""Reading committed run records into observations.

The adapter the whole history rests on. The handoff's reason for owning an observation
store at all is that eval platforms delete traces on lower tiers, so a scheduled pull
appends to a table that keeps them forever — and committed run files are the one source
whose history cannot evaporate, which is why the backfill starts here.

**Two decisions worth stating, because neither is obvious from the specification.**

*Observations are keyed by metric, not by clause.* Every candidate here carries
`clause_ref = None`, and the clause-to-metric relationship lives in `binding`. PRD B2 puts
`clause_ref` on the observation, and taken literally that would mean binding a metric to a
second clause required rewriting history — and H5, one metric legitimately serving two
clauses, would double every stored row. With the relationship in `binding`, H5 costs
nothing and the idempotency key collapses to what it should be:
`(org, product, metric, run_url)`. The column stays for a source that names a clause
itself, such as a production metric mapped directly.

*A file that is not a run is not a failure.* The distinction is the point of this adapter.
`not_a_run_record` means the document was never a run; `Failed` means one was and did not
import. Collapsing them is how AC-2's reconciliation comes to compare 47 against 47 and
look correct while a run is missing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from layer.adapters.base import (
    Failed,
    ImportReport,
    ObservationCandidate,
    Skipped,
    SourceDocument,
)
from layer.metrics import MetricValue, Unmeasurable, compute
from layer.metrics.pointer import MISSING, resolve


#: The keys that say where a case's identity, its input and its trace pointer live in a
#: row. A reader may set them once for all its metrics; a metric definition may override
#: any of them. Mechanism lives in the engine, shape lives here (agnosticism rule R2).
#:
#: The set is closed and an unknown key is refused rather than ignored, because the
#: failure mode of a typo is silent: `input_fields` instead of `input_field` would leave
#: every failing case with no input and the finding with nothing to show, and nothing
#: anywhere would say why.
CASE_FIELDS = frozenset({
    "id_field",
    "input_field",
    "trace_id_field",
    "trace_url_field",
    "error_field",
    "redact_patterns",
})


@dataclass(frozen=True)
class Reader:
    """One family of files inside an eval source, and how to read it.

    A source often has more than one: a run record plus a judge's verdict written beside
    it. A reader with `join_on` set inherits its provenance from the primary run it names,
    which is how a sidecar carrying only `{cases, grounded}` becomes an observation with a
    timestamp at all.
    """

    glob: str
    metrics: tuple[dict, ...]
    run_id: str = "/meta/run_id"
    measured_at: str = "/meta/timestamp_utc"
    prompt_version: str | None = "/meta/prompt_version"
    corpus_sha: str | None = "/meta/corpus_sha"
    code_rev: str | None = "/meta/git_sha"
    trace_url: str | None = None
    join_on: str | None = None
    #: Pointers that must resolve for the document to be a run of this kind at all.
    requires: tuple[str, ...] = ()
    #: Where the per-case layer lives in a row, for every metric this reader computes.
    #: See `CASE_FIELDS`. A run file's rows have one shape, and repeating `input_field`
    #: on each of a source's metrics is how one of them comes to be forgotten.
    cases: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> Reader:
        return cls(
            glob=raw.get("glob", "*"),
            metrics=tuple(raw.get("metrics", ())),
            run_id=raw.get("run_id", "/meta/run_id"),
            measured_at=raw.get("measured_at", "/meta/timestamp_utc"),
            prompt_version=raw.get("prompt_version", "/meta/prompt_version"),
            corpus_sha=raw.get("corpus_sha", "/meta/corpus_sha"),
            code_rev=raw.get("code_rev", "/meta/git_sha"),
            trace_url=raw.get("trace_url"),
            join_on=raw.get("join_on"),
            requires=tuple(raw.get("requires", ())),
            cases=_case_fields(raw.get("cases") or {}),
        )

    @property
    def is_sidecar(self) -> bool:
        return self.join_on is not None

    def definition(self, metric: dict) -> dict:
        """One metric definition with this reader's case shape beneath it.

        The reader's keys are defaults and the definition wins, because the shape is a
        property of the rows while an exception is a property of one metric — a judge's
        sidecar that names its rows differently, say.
        """
        if not self.cases:
            return metric
        return {**self.cases, **metric}


def _case_fields(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("a reader's 'cases' must be an object of field names")
    unknown = sorted(set(raw) - CASE_FIELDS)
    if unknown:
        raise ValueError(
            f"a reader's 'cases' does not take {', '.join(unknown)}. "
            f"It takes: {', '.join(sorted(CASE_FIELDS))}. A key that was ignored here "
            f"would leave every failing case with no evidence and say nothing about it."
        )
    return dict(raw)


@dataclass
class Provenance:
    """What a run says about the inputs that produced it.

    None of it is interpreted. PRD H1 requires the Layer to say "the shas are identical
    and the scores differ" and never to explain why, so these are recorded and compared,
    never reasoned from.
    """

    run_id: str | None
    measured_at: datetime
    prompt_version: str | None = None
    corpus_sha: str | None = None
    code_rev: str | None = None


class RunFilesAdapter:
    """`role = "eval"`. Committed run documents in, observation candidates out."""

    role = "eval"

    def read(self, documents: Iterable[SourceDocument], config: dict) -> ImportReport:
        readers = [Reader.from_dict(r) for r in config.get("readers", [])]
        if not readers:
            raise ValueError("an eval source needs at least one reader")

        report = ImportReport(source_role=self.role)
        docs = list(documents)
        report.enumerated = len(docs)

        # Primary readers first, so a sidecar has provenance to join to. Processing in one
        # pass would make the result depend on filesystem ordering.
        provenance: dict[str, Provenance] = {}
        deferred: list[tuple[SourceDocument, Reader, Any]] = []

        for document in docs:
            reader = self._reader_for(document.identifier, readers)
            if reader is None:
                report.skipped.append(
                    Skipped(document.identifier, "no_reader_matched",
                            "no reader's glob covers this path")
                )
                continue

            parsed = self._parse(document)
            if isinstance(parsed, Failed):
                report.failed.append(parsed)
                continue

            if not self._looks_like_a_run(parsed, reader):
                # Never a run of this kind. Distinct from a failure, and counted so the
                # reconciliation that proves AC-2 stays meaningful.
                report.skipped.append(
                    Skipped(document.identifier, "not_a_run_record",
                            "the pointers this reader requires do not resolve, so this "
                            "document is not a run of this kind")
                )
                continue

            if reader.is_sidecar:
                deferred.append((document, reader, parsed))
                continue

            outcome = self._observations(document, reader, parsed, None, report)
            if outcome is None:
                continue
            provenance[outcome.run_id or document.identifier] = outcome
            report.imported += 1

        for document, reader, parsed in deferred:
            key = resolve(parsed, reader.join_on or "")
            primary = provenance.get(str(key)) if key is not MISSING else None
            if primary is None:
                report.failed.append(
                    Failed(document.identifier, "unjoined_sidecar",
                           f"no primary run matches {reader.join_on}={key!r}, so this "
                           f"measurement cannot be placed in time")
                )
                continue
            if self._observations(document, reader, parsed, primary, report) is not None:
                report.imported += 1

        self._summarise(report)
        return report

    # -- per document -------------------------------------------------------------

    def _reader_for(self, path: str, readers: list[Reader]) -> Reader | None:
        """The most specific matching reader.

        Sidecars are matched before primaries: `*-groundedness.json` also matches
        `*.json`, and the broader pattern winning would read a judge's verdict as a run.
        """
        from pathlib import PurePosixPath

        matched = [r for r in readers if PurePosixPath(path).full_match(r.glob)]
        if not matched:
            return None
        return max(matched, key=lambda r: (r.is_sidecar, len(r.glob)))

    def _parse(self, document: SourceDocument) -> Any | Failed:
        raw = document.content
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            return Failed(document.identifier, "unparsable_json", str(exc))

    def _looks_like_a_run(self, parsed: Any, reader: Reader) -> bool:
        if not isinstance(parsed, dict):
            return False
        required = reader.requires or tuple(
            p for p in (reader.run_id, reader.measured_at if not reader.is_sidecar else None)
            if p
        )
        return all(resolve(parsed, pointer) is not MISSING for pointer in required)

    def _observations(
        self,
        document: SourceDocument,
        reader: Reader,
        parsed: Any,
        inherited: Provenance | None,
        report: ImportReport,
    ) -> Provenance | None:
        provenance = inherited or self._provenance(parsed, reader)
        if isinstance(provenance, Failed):
            report.failed.append(provenance)
            return None
        if inherited is not None:
            run_id = resolve(parsed, reader.run_id)
            provenance = Provenance(
                run_id=str(run_id) if run_id is not MISSING else inherited.run_id,
                measured_at=inherited.measured_at,
                prompt_version=inherited.prompt_version,
                corpus_sha=inherited.corpus_sha,
                code_rev=inherited.code_rev,
            )

        url = document.url
        if reader.trace_url:
            from_doc = resolve(parsed, reader.trace_url)
            if from_doc is not MISSING and from_doc:
                url = str(from_doc)

        produced = 0
        for metric in reader.metrics:
            definition = reader.definition(metric)
            result = compute(parsed, definition)
            if isinstance(result, Unmeasurable):
                report.unmeasured.append(
                    Skipped(f"{document.identifier}#{result.metric}", result.reason, result.note)
                )
                continue
            report.candidates.append(self._candidate(result, provenance, url))
            produced += 1

        if produced == 0:
            report.failed.append(
                Failed(document.identifier, "no_metric_computed",
                       "this is a run of the expected shape but none of the source's "
                       "metric definitions could be computed from it")
            )
            return None
        return provenance

    def _provenance(self, parsed: Any, reader: Reader) -> Provenance | Failed:
        measured = self._timestamp(resolve(parsed, reader.measured_at))
        if measured is None:
            return Failed(
                "run", "no_timestamp",
                f"nothing usable at {reader.measured_at}. An observation with no time "
                f"cannot sit in a series, so it is not stored rather than being dated now",
            )
        run_id = resolve(parsed, reader.run_id)
        return Provenance(
            run_id=str(run_id) if run_id is not MISSING else None,
            measured_at=measured,
            prompt_version=self._text(parsed, reader.prompt_version),
            corpus_sha=self._text(parsed, reader.corpus_sha),
            code_rev=self._text(parsed, reader.code_rev),
        )

    def _candidate(
        self, value: MetricValue, provenance: Provenance, url: str | None
    ) -> ObservationCandidate:
        return ObservationCandidate(
            metric=value.metric,
            value=value.value,
            measured_at=provenance.measured_at,
            source_kind="eval",
            # Left unbound on purpose. See the module docstring: the clause relationship
            # lives in `binding`, so one metric can serve two clauses for free.
            clause_ref=None,
            passed=value.passed,
            total=value.total,
            unit=value.unit,
            prompt_version=provenance.prompt_version,
            corpus_sha=provenance.corpus_sha,
            code_rev=provenance.code_rev,
            run_id=provenance.run_id,
            run_url=url,
            detail=value.detail,
            # The per-case layer, carried as the engine produced it. Nothing is
            # recomputed here: the cases and the number are one measurement, and a
            # second pass over the rows could only make them disagree.
            cases=value.cases,
        )

    # -- helpers ------------------------------------------------------------------

    def _text(self, parsed: Any, pointer: str | None) -> str | None:
        if not pointer:
            return None
        found = resolve(parsed, pointer)
        if found is MISSING or found is None:
            return None
        if isinstance(found, dict):
            # A nested shape such as {"commit": "...", "dirty": true}. Flattened to the
            # text a human would recognise, with the dirty marker kept: it is provenance,
            # and it is also what stops a citation being built from it.
            commit = found.get("commit") or found.get("sha") or ""
            return f"{commit}-dirty" if found.get("dirty") else str(commit) or None
        return str(found)

    def _timestamp(self, raw: Any) -> datetime | None:
        if raw is MISSING or raw is None:
            return None
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=UTC)
        text = str(raw).strip()
        for attempt in (text, text.replace("Z", "+00:00")):
            try:
                parsed = datetime.fromisoformat(attempt)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                continue
        # `20260915-133223Z`, the shape a run id uses when it is also the clock.
        try:
            parsed = datetime.strptime(text[:16], "%Y%m%d-%H%M%S")
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            return None

    def _summarise(self, report: ImportReport) -> None:
        disagreeing = report.cases_disagreeing
        if disagreeing:
            report.notes.append(
                f"{len(disagreeing)} measurement(s) carry cases that do not reproduce "
                f"their own passed/total and will not be stored as evidence: "
                + ", ".join(disagreeing[:5])
            )
        if report.failed:
            report.notes.append(
                f"{len(report.failed)} document(s) did not import: "
                + ", ".join(f"{f.identifier} ({f.reason})" for f in report.failed[:5])
            )
        if report.unmeasured:
            from collections import Counter

            by_metric = Counter(s.identifier.split("#", 1)[-1] for s in report.unmeasured)
            report.notes.append(
                "metrics not computable in some runs: "
                + ", ".join(f"{m} in {n}" for m, n in sorted(by_metric.items()))
            )
