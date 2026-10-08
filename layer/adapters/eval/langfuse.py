"""Langfuse as an eval source.

This file contains no measurement code, which is the point. It normalises a dataset run
into the same `{meta, results}` document a committed run file already has, and hands it to
`RunFilesAdapter`. One measurement path, two transports, and a metric definition written
for one works for the other.

    documents = LangfuseSource(client).documents(config)
    report = RunFilesAdapter().read(documents, config)

**Why history is pulled here at all.** The handoff's reason for owning an observation
store is that Langfuse and Braintrust delete traces on lower tiers, often after 30 to 90
days, so a metric computed live today is unanswerable next quarter. Every run this fetches
is appended to a table that keeps it forever.

**The API surface below was read, not recalled.** `Langfuse().api` is a `LangfuseAPI`
exposing `datasets.get_runs(dataset_name, page, limit)`,
`datasets.get_run(dataset_name, run_name)` returning items with `trace_id`, and
`scores.get_many(..., dataset_run_id=...)` returning a discriminated union of numeric,
categorical, boolean, correction and text scores. Verified against langfuse 4.15.4.

**What is not verified.** No live instance was reached while writing this, so pagination
behaviour at scale, rate limits and the exact timestamp types are untested against a real
project. The client is injected for exactly that reason: the normalisation below is
testable without a network, and the remaining risk is confined to one seam. Recorded as an
open item in PROGRESS.md.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from layer.adapters.base import SourceDocument


class LangfuseLike(Protocol):
    """Only what is used, so a fake in a test is three attributes deep."""

    @property
    def api(self) -> Any: ...


@dataclass(frozen=True)
class LangfuseConfig:
    """Where the runs are and what to carry out of them.

    `prompt_version_key` and `corpus_sha_key` name entries in a run's own `metadata`,
    because what a team records there is theirs to choose. Absent is fine: provenance the
    source does not carry simply is not recorded, and H1 then reports that versioning
    cannot explain a movement rather than inventing a version.
    """

    dataset: str
    host: str | None = None
    project_id: str | None = None
    runs: tuple[str, ...] = ()
    prompt_version_key: str = "prompt_sha"
    corpus_sha_key: str = "corpus_sha"
    code_rev_key: str = "git_sha"
    limit: int = 50

    @classmethod
    def from_dict(cls, config: dict) -> LangfuseConfig:
        raw = config.get("langfuse", config)
        return cls(
            dataset=raw["dataset"],
            host=raw.get("host"),
            project_id=raw.get("project_id"),
            runs=tuple(raw.get("runs", ())),
            prompt_version_key=raw.get("prompt_version_key", "prompt_sha"),
            corpus_sha_key=raw.get("corpus_sha_key", "corpus_sha"),
            code_rev_key=raw.get("code_rev_key", "git_sha"),
            limit=int(raw.get("limit", 50)),
        )


class LangfuseSource:
    """Dataset runs into documents. The client is injected and never constructed here."""

    def __init__(self, client: LangfuseLike) -> None:
        self._client = client

    def documents(self, config: dict) -> list[SourceDocument]:
        settings = LangfuseConfig.from_dict(config)
        return list(self._documents(settings))

    def _documents(self, settings: LangfuseConfig) -> Iterator[SourceDocument]:
        for run in self._runs(settings):
            name = _attr(run, "name")
            if settings.runs and name not in settings.runs:
                continue
            detail = self._client.api.datasets.get_run(
                dataset_name=settings.dataset, run_name=name
            )
            document = self._normalise(detail, settings)
            yield SourceDocument(
                # Path-shaped, so a reader glob matches it with the same rule that
                # matches a file path. An identifier that mixed separators would need
                # its own matching rule, and then two sources would disagree about
                # what a glob means.
                identifier=f"langfuse/{settings.dataset}/{name}",
                content=json.dumps(document, default=str),
                url=self._run_url(settings, _attr(detail, "id")),
            )

    def _runs(self, settings: LangfuseConfig) -> Iterable[Any]:
        page = 1
        while True:
            response = self._client.api.datasets.get_runs(
                dataset_name=settings.dataset, page=page, limit=settings.limit
            )
            batch = _attr(response, "data") or []
            yield from batch
            # Stop on a short page rather than trusting a reported total: a total that
            # disagrees with the data would otherwise loop forever or truncate history,
            # and AC-2 forbids omitting a run.
            if len(batch) < settings.limit:
                return
            page += 1

    # -- normalisation -------------------------------------------------------------

    def _normalise(self, run: Any, settings: LangfuseConfig) -> dict:
        items = _attr(run, "dataset_run_items") or []
        scores_by_trace = self._scores_for(_attr(run, "id"))
        metadata = _attr(run, "metadata") or {}

        results = []
        for item in items:
            trace_id = _attr(item, "trace_id")
            results.append({
                "id": _attr(item, "dataset_item_id") or _attr(item, "id"),
                "trace_id": trace_id,
                "trace_url": self._trace_url(settings, trace_id),
                # Scores arrive as a list of name/value pairs and are reshaped into one
                # object per row, so a metric definition can address `/scores/<name>`
                # exactly as it addresses any other field.
                "scores": scores_by_trace.get(trace_id, {}),
            })

        return {
            "meta": {
                "run_id": _attr(run, "name"),
                "started": _attr(run, "created_at"),
                "dataset": _attr(run, "dataset_name") or settings.dataset,
                "cases": len(results),
                "prompt_sha": _get(metadata, settings.prompt_version_key),
                "corpus_sha": _get(metadata, settings.corpus_sha_key),
                "git_sha": _get(metadata, settings.code_rev_key),
            },
            "results": results,
        }

    def _scores_for(self, dataset_run_id: Any) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            response = self._client.api.scores.get_many(
                dataset_run_id=dataset_run_id, page=page, limit=100
            )
            batch = _attr(response, "data") or []
            for score in batch:
                trace_id = _attr(score, "trace_id")
                name = _attr(score, "name")
                if not trace_id or not name:
                    continue
                out.setdefault(trace_id, {})[name] = _score_value(score)
            if len(batch) < 100:
                return out
            page += 1

    def _trace_url(self, settings: LangfuseConfig, trace_id: Any) -> str | None:
        if not (settings.host and settings.project_id and trace_id):
            return None
        return f"{settings.host.rstrip('/')}/project/{settings.project_id}/traces/{trace_id}"

    def _run_url(self, settings: LangfuseConfig, run_id: Any) -> str | None:
        if not (settings.host and settings.project_id and run_id):
            return None
        return (
            f"{settings.host.rstrip('/')}/project/{settings.project_id}"
            f"/datasets/{settings.dataset}/runs/{run_id}"
        )


# -- shape helpers ---------------------------------------------------------------


def _attr(obj: Any, name: str) -> Any:
    """Read a field from a pydantic model or a plain dict.

    The SDK returns models; a fixture or a fake returns dicts. Supporting both keeps the
    tests free of the SDK, which is what makes the normalisation testable at all.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _get(metadata: Any, key: str) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _score_value(score: Any) -> Any:
    """One comparable value from any of Langfuse's five score types.

    A boolean or categorical score is left as it arrives rather than coerced to a number
    here, because what counts as a pass belongs in the metric definition a human
    confirmed, not in a transport.
    """
    value = _attr(score, "value")
    if value is not None:
        return value
    return _attr(score, "string_value") or _attr(score, "comment")
