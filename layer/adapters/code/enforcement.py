"""Finding out what CI actually checks, as opposed to what the specification says.

This is the adapter condition 1 depends on, and the reason the product has anything to
find. A gate can check the right metric at the right threshold against the wrong set of
runs: a breach sitting mid-sequence then never fails a build and never appears on a
dashboard. `enforced: true, scope: latest_only` is that condition, and a boolean
`enforced` cannot express it (PRD H14, AC-15).

**It searches for stated bars; it does not comprehend code.** The scan is given the
product's threshold clauses and asks, for each, whether a CI file compares that number to
something bearing that name. Understanding an arbitrary gate script in general is a
research problem; answering "is 0.85 compared against something called team accuracy" is a
search, and a search is what generalises across repositories nobody has seen.

**It is conservative in one direction only.** Where it cannot read something confidently it
says so — `partial` with a note, or `scope: undetermined` — and never guesses. The cost of
a false "enforced" is a real gap left hidden, which is the failure this whole product
exists to prevent; the cost of an honest "could not tell" is a human reading one file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from layer.adapters.base import (
    EnforcementCandidate,
    Expectation,
    Failed,
    ImportReport,
    Skipped,
    SourceDocument,
)

#: Selectors that narrow a collection of runs to one. Any of these in a file that also
#: looks at runs means the gate is blind to everything but the newest.
_NARROWING = (
    (r"\[\s*-1\s*\]", "indexes the last element of the run list"),
    (r"\.pop\(\s*\)", "pops the last run off the list"),
    (r"\bmax\s*\(", "takes a single maximum rather than checking each run"),
    (r"\b(?:latest|newest|most_recent)", "selects a run described as the latest"),
    (r"\|\s*tail\s+-n?\s*1|\btail\s+-1\b", "pipes to tail -1"),
    (r"\.head\(\s*1\s*\)|\.first\(\s*\)|\.last\(\s*\)", "takes a single row"),
    (r"\border_by\([^)]*desc[^)]*\)\s*\.\s*limit\(\s*1\s*\)", "orders descending and limits to one"),
)

#: Signs a file walks every run rather than one.
_ITERATING = (
    r"\bfor\s+\w+\s+in\s+[^:\n]*(?:runs?|files?|records?|glob|iterdir|listdir)",
    r"\bfor\s+\w+\s+in\s+sorted\(",
    r"\.map\(|\.forEach\(|\bevery\(|\ball\(\s*\w+\s+for\b",
)

#: Signs a file reads a collection of runs at all. Without one, a narrowing selector is
#: probably unrelated to run selection and should not be read as one.
_RUN_COLLECTION = (
    r"\.glob\(|\biglob\(|\blistdir\(|\biterdir\(|\bruns?\b|\brun_files\b|\bresults?\b"
)

#: A check that runs but cannot fail the build. Worth detecting because it looks enforced
#: in every dashboard and is not.
#:
#: Each of these disables the check it sits with, not every check in the file: a
#: decorator belongs to one function, a `|| true` to one command, a step's
#: `continue-on-error` to that step. Read file-wide, one known gap reports every other
#: check around it as toothless, which is a false `enforced: false` and therefore a
#: proposal to fix a gate that works.
_TOOTHLESS = (
    (r"continue-on-error\s*:\s*true", "the CI step is marked continue-on-error"),
    (r"\|\|\s*true\b", "the command's failure is swallowed by `|| true`"),
    (r"\bset\s+\+e\b", "the shell stops failing on error"),
    (r"@pytest\.mark\.(?:skip|xfail)", "the check is skipped or expected to fail"),
    (r"#\s*(?:TODO|FIXME).{0,40}\b(?:enable|re-?enable)\b", "the check is disabled pending work"),
)

#: A line that names a metric in order to leave it out. A gate that drops one metric's
#: runs before reading a record -- `if "<metric>" not in path.name` -- names it on that
#: line, and matching there read the exclusion as a check: the one metric the gate
#: deliberately does not cover came back enforced. That is a real gap hidden, the
#: failure this adapter exists to prevent, so an exclusion is never a check.
_EXCLUDING = (
    r"\bnot\s+in\b",
    r"!=",
)

_COMPARATOR = re.compile(r"(>=|<=|==|!=|<|>)")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%?")


@dataclass(frozen=True)
class CodeConfig:
    """Which files are CI, and which of them can fail a build."""

    files: tuple[str, ...] = ()
    #: Lines around a name match to search for its threshold. A gate often names a
    #: constant on one line and compares it several lines later.
    window: int = 4
    #: What the code calls a metric, where that differs from what the spec calls it.
    #: A real and unavoidable case: a gate may enforce escalation recall by counting
    #: `missed` escalations and requiring zero, never writing the words "escalation
    #: recall" anywhere. Without this the scan reports the clause as unenforced, which
    #: is a finding that is simply wrong. Config rather than code, and confirmed by the
    #: same human who confirms a binding, because it is the same kind of assertion.
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> CodeConfig:
        raw = config.get("metric_aliases") or {}
        return cls(
            files=tuple(config.get("files", ())),
            window=int(config.get("window", 4)),
            aliases={k: tuple(v) for k, v in raw.items()},
        )


class EnforcementAdapter:
    """`role = "code"`. CI documents plus stated bars in, enforcement facts out."""

    role = "code"

    def read(
        self,
        documents: Iterable[SourceDocument],
        config: dict,
        expectations: Iterable[Expectation] = (),
    ) -> ImportReport:
        settings = CodeConfig.from_dict(config)
        wanted = list(expectations)
        report = ImportReport(source_role=self.role)
        docs = list(documents)
        report.enumerated = len(docs)

        if not wanted:
            # Nothing stated means nothing to look for. Saying so beats returning an empty
            # report that reads like "CI checks nothing".
            report.notes.append(
                "no threshold clauses were supplied, so enforcement was not assessed. "
                "This is not the same as finding that CI enforces nothing."
            )
            report.skipped.extend(Skipped(d.identifier, "no_expectations") for d in docs)
            return report

        for document in docs:
            try:
                found = self._scan(document, settings, wanted, report)
            except Exception as exc:
                report.failed.append(
                    Failed(document.identifier, "unscannable",
                           f"{type(exc).__name__}: {exc}")
                )
                continue
            if found:
                report.candidates.extend(found)
                report.imported += 1
            else:
                report.skipped.append(
                    Skipped(document.identifier, "no_threshold_found",
                            "no stated bar was matched in this file")
                )
        return report

    # -- per file -----------------------------------------------------------------

    def _scan(
        self,
        document: SourceDocument,
        settings: CodeConfig,
        wanted: list[Expectation],
        report: ImportReport,
    ) -> list[EnforcementCandidate]:
        raw = document.content
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        lines = text.splitlines()

        scope, scope_note = self._scope(text)

        out: list[EnforcementCandidate] = []
        for expectation in wanted:
            hit = self._match(expectation, lines, settings)
            if hit is None:
                continue
            line_no, threshold, comparator, note = hit
            toothless = self._toothless(_block_around(lines, line_no - 1))

            partial_note = note
            if scope_note and scope != "all_runs":
                partial_note = "; ".join(filter(None, (partial_note, scope_note)))
            if toothless:
                partial_note = "; ".join(filter(None, (partial_note, toothless)))

            out.append(EnforcementCandidate(
                metric=expectation.metric,
                file=document.identifier,
                # A toothless check runs and cannot fail the build, which is not
                # enforcement however it looks on a dashboard.
                enforced=not toothless,
                partial=bool(partial_note),
                partial_note=partial_note or None,
                threshold=threshold,
                comparator=comparator or expectation.comparator,
                scope=scope,
                line=line_no,
                unit=document.identifier.rsplit("/", 1)[-1],
                known_failing=bool(toothless),
            ))
        return out

    def _match(
        self, expectation: Expectation, lines: list[str], settings: CodeConfig
    ) -> tuple[int, float | None, str | None, str | None] | None:
        """Where this metric is checked, and at what value.

        Returns None when the file says nothing about it — absence is reported by the
        finding query as an unenforced clause, not by a row claiming `enforced: false`,
        so there is nothing to emit here.
        """
        window = settings.window
        name = _name_pattern(
            expectation.metric, expectation.label, settings.aliases.get(expectation.metric)
        )
        # A comparison beats a mention, wherever each sits. A file names a metric in its
        # imports, its docstring and its comments before it ever checks one, and taking
        # the first line that matched reported the import as the check -- a bar read as
        # unreadable, and in a file with a marker, as toothless.
        #
        # Where no line carries a readable number -- `assert invalid == []` enforces a
        # 100% bar without writing 100 anywhere -- a mention is still the honest
        # answer, and among mentions the one that compares something beats the one
        # that only names it. The earliest wins within each of those tiers.
        mention: tuple[int, None, str | None, str] | None = None

        for index, line in enumerate(lines):
            if not name.search(line):
                continue
            if any(re.search(p, line) for p in _EXCLUDING):
                continue
            line_no = index + 1
            start, end = max(0, index - window), min(len(lines), index + window + 1)
            neighbourhood = lines[start:end]

            threshold, comparator = _threshold_for(expectation, line, neighbourhood)
            if threshold is not None:
                note = None
                if expectation.value is not None and not _same(threshold, expectation.value):
                    # Checked, but not at the stated number. A real and common condition:
                    # the spec moved and the gate did not.
                    note = (
                        f"the check compares against {threshold:g}, while the clause "
                        f"states {expectation.value:g}"
                    )
                return line_no, threshold, comparator, note
            if mention is None or (comparator and not mention[2]):
                mention = (
                    line_no,
                    None,
                    comparator,
                    "a check naming this metric exists, but no literal matching the "
                    "stated threshold was found nearby, so the enforced value could "
                    "not be read",
                )
        return mention

    # -- file-level reads ----------------------------------------------------------

    def _scope(self, text: str) -> tuple[str, str | None]:
        """Which run set the file checks.

        Narrowing wins over iteration: a file may glob every run and then index the last
        one, which is precisely the shape that hides a mid-sequence breach.
        """
        looks_at_runs = re.search(_RUN_COLLECTION, text, re.IGNORECASE) is not None

        for pattern, why in _NARROWING:
            if re.search(pattern, text, re.IGNORECASE) and looks_at_runs:
                return "latest_only", (
                    f"the check {why}, so a breach in any earlier run is never seen"
                )

        if any(re.search(p, text, re.IGNORECASE) for p in _ITERATING):
            return "all_runs", None

        return "undetermined", (
            "the run set this check reads could not be determined from the file, so "
            "whether an earlier breach would be caught is unknown"
        )

    def _toothless(self, text: str) -> str | None:
        for pattern, why in _TOOTHLESS:
            if re.search(pattern, text, re.IGNORECASE):
                return f"{why}, so this check cannot fail a build"
        return None


# -- matching helpers ------------------------------------------------------------


def _block_around(lines: list[str], index: int) -> str:
    """The span of the check at `index`, read by indentation.

    A marker that disables a check sits with that check: a decorator on the line above
    its function, a `|| true` on the command itself, a `continue-on-error` inside the
    step it belongs to. Reading the whole file instead lets one acknowledged gap report
    every other check around it as unable to fail a build -- the reference policy gate
    carries one `xfail(strict=True)` under a heading that says the rest block the build
    today, and file-wide reading called all four of its checks toothless.

    Indentation rather than a parser, because this has to hold for Python, YAML and a
    shell script alike without knowing which it was handed. Where a file has no
    structure to read -- a flat script whose checks all sit at the left margin -- the
    block is the whole file, which for that file is the only honest answer.
    """
    if not lines or not 0 <= index < len(lines):
        return "\n".join(lines)

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    own = indent(lines[index])

    # A name often matches the check's own header -- `def test_team_accuracy` -- rather
    # than a line inside it. A header is recognised by what follows it being indented
    # under it, which holds for Python, YAML and a braced language alike.
    following = next(
        (indent(l) for l in lines[index + 1:] if l.strip()), None
    )
    if following is not None and following > own:
        header = index
    else:
        header = next(
            (j for j in range(index - 1, -1, -1)
             if lines[j].strip() and indent(lines[j]) < own),
            None,
        )
    if header is None:
        # Nothing encloses this line and it opens nothing. The file is its own block.
        return "\n".join(lines)

    start, depth = header, indent(lines[header])
    while start and lines[start - 1].strip()[:1] in ("@", "#") and (
        indent(lines[start - 1]) == depth
    ):
        start -= 1

    end = len(lines)
    for k in range(index + 1, len(lines)):
        if lines[k].strip() and indent(lines[k]) <= depth:
            end = k
            break
    return "\n".join(lines[start:end])



def _name_pattern(
    metric: str, label: str | None, aliases: tuple[str, ...] | None = None
) -> re.Pattern[str]:
    """A pattern for a metric name as code spells it.

    `answer_accuracy` has to find `MIN_ANSWER_ACCURACY`, `answerAccuracy`, `answer-accuracy` and
    `"team accuracy"`. Words in order, separated by anything that is not a letter or a
    digit, which also keeps `accuracy_team` from matching.

    Aliases are alternatives, not extra words, because a gate enforcing a clause under a
    different name usually shares no vocabulary with it at all.
    """
    spellings = [metric, *(aliases or ())]
    if label:
        spellings.append(label)

    branches = []
    for spelling in spellings:
        words = [w for w in re.split(r"[^a-z0-9]+", str(spelling).lower()) if w]
        if words:
            branches.append(r"[^a-z0-9]{0,3}".join(re.escape(w) for w in words))
    if not branches:
        return re.compile(r"(?!)")
    return re.compile("|".join(branches), re.IGNORECASE)


def _threshold_for(
    expectation: Expectation, line: str, neighbourhood: list[str]
) -> tuple[float | None, str | None]:
    """The number this check compares against, read in two steps of falling confidence.

    *On the same line as the metric's name.* `MIN_ANSWER_ACCURACY = 0.85` names both at
    once, so a number here is almost certainly the bar — including when it is **not** the
    stated one, which is how "the spec moved and the gate did not" becomes visible.

    *The stated value elsewhere in the neighbourhood.* Weaker, so only the stated value
    counts: finding 0.85 three lines from a mention of team accuracy confirms the bar, but
    finding some other number there confirms nothing.

    What it deliberately will not do is pick a number because it was nearby. An earlier
    version fell back to "the first ratio between 0 and 1", which would have reported a
    threshold of 0.5 for a metric whose gate happened to sit beside a sampling constant.
    A fabricated threshold is worse than an unread one: it reads as knowledge.
    """
    comparator = None
    for candidate_line in [line, *neighbourhood]:
        found = _COMPARATOR.search(candidate_line)
        if found:
            comparator = found.group(1)
            break

    # A stated bar of 0, 1 or 100 cannot be confirmed by a bare digit even on the same
    # line: `broken = sum(1 for r in results if r["problem"])` names the metric and
    # contains a 1, and reading that as a threshold reported a gate as enforcing contract
    # validity at 1.0 when the digit found was a loop accumulator. For those values the
    # number must sit where a threshold sits — beside a comparator, or alone on the right
    # of an assignment.
    common = expectation.value is not None and _too_common(expectation.value)
    pool = _threshold_shaped_numbers(line) if common else _numbers_in(line)

    if pool:
        if expectation.value is not None:
            for number in pool:
                if _same(number, expectation.value):
                    return expectation.value, comparator
        for number in pool:
            if not _too_common(number):
                return number, comparator

    if expectation.value is not None and not _too_common(expectation.value):
        for candidate_line in neighbourhood:
            for number in _numbers_in(candidate_line):
                if _same(number, expectation.value):
                    return expectation.value, comparator

    return None, comparator


#: Values whose digits appear everywhere in ordinary code.
_COMMON = (0.0, 1.0, 100.0)


def _too_common(value: float) -> bool:
    """Whether a stated value is too ordinary to confirm by proximity.

    A bar of 100% is stated as `1.0` or `100`, and both appear constantly in code that has
    nothing to do with thresholds — `sum(1 for r in results ...)` is enough to make a
    neighbourhood match. Confirming a 100% bar that way reported the gate as enforcing
    contract validity at 1.0 when the digit found was a loop accumulator. For these values
    the name and the number must share a line, and otherwise the enforced value is reported
    as unread, which is true: the gate does check the thing, and it never writes the
    number down.
    """
    return any(abs(value - common) < 1e-9 for common in _COMMON)


def _numbers_in(line: str) -> list[float]:
    out: list[float] = []
    for raw in _NUMBER.findall(line):
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


#: A number beside a comparator, or alone on the right of an assignment. Where a threshold
#: is written, as opposed to anywhere a digit may appear.
_BESIDE_COMPARATOR = re.compile(r"(?:>=|<=|==|!=|<|>)\s*(\d+(?:\.\d+)?)")
_ASSIGNED_ALONE = re.compile(r"[:=]\s*(\d+(?:\.\d+)?)\s*%?\s*[,)\]}]?\s*(?:#.*)?$")


def _threshold_shaped_numbers(line: str) -> list[float]:
    out: list[float] = []
    for pattern in (_BESIDE_COMPARATOR, _ASSIGNED_ALONE):
        for raw in pattern.findall(line):
            try:
                out.append(float(raw))
            except ValueError:
                continue
    return out


def _same(found: float, stated: float) -> bool:
    """Equal allowing for scale and for floating point.

    85 and 0.85 are the same bar written two ways, and a gate is as likely to hold one as
    the other.
    """
    for candidate in (found, found / 100, found * 100):
        if abs(candidate - stated) < 1e-9:
            return True
    return False
