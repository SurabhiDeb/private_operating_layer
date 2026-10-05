"""The acceptance matrix. AC-10, AC-11, AC-12.

Three criteria ask the same question in three vocabularies: does every hard case, every
user-story criterion and every edge case have a test? Until this file existed the answer
could only be assembled by hand, which is why it was wrong — `PROGRESS.md` said the
specification held 36 acceptance criteria when it holds 39, and no test anywhere carried
the `story` marker that `pytest.ini` had declared from the start.

**The refs come from the specification, not from a list kept here.** A criterion added to
PRD Part C with no test appears as a failure the next time this runs, which is the only
version of this file worth having: one with its own copy of the list would agree with
itself forever.

**A deferral is a declaration, not an exemption.** Everything in `DEFERRED` names the phase
that will cover it and why it cannot be covered now, and a deferral for something that
*is* covered fails too — so the list cannot quietly rot into an excuse. Nothing is skipped
and nothing is marked `xfail`: the suite reports what is true today.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent.parent / "operating_layer_main" / "PRD-SPEC.md"
TESTS = Path(__file__).resolve().parent

#: Phase 5 onwards. Each entry is (phase, why it cannot be covered in phases 1 to 3).
#: The reason has to name what is missing, not merely which phase owns it.
DEFERRED: dict[str, tuple[str, str]] = {
    # -- acceptance criteria -----------------------------------------------------
    "AC-6": ("phase 6", "the walk is tested over a six-link chain in tests/test_mcp.py; "
                        "no bindable source produces requirements, decisions or tickets, "
                        "so a real product's chain cannot be complete yet"),
    "AC-16": ("phase 5", "twenty proposals decided by a person. Needs the decision flow "
                         "and a human's time; the number has to land where it lands"),
    "AC-34": ("phase 5", "proposal.rank and the signals behind it, which the harvest "
                         "generator produces"),
    "AC-35": ("phase 5", "a harvested case surviving into an eval suite with its "
                         "provenance"),
    "AC-36": ("phase 5", "harvested_case_yield over a full eval cycle"),
    # -- hard cases --------------------------------------------------------------
    "H2": ("phase 5", "the detection is tested; invalidating an open proposal when a "
                      "human edits its target is part of the decision flow"),
    "H7": ("phase 5", "the trace expiry is tested; marking a proposal evidence-expired "
                      "needs the proposal lifecycle"),
    "H10": ("phase 6", "the refusal is tested; elapsed time on a stalled decision needs "
                       "a decision source"),
    # -- edge cases --------------------------------------------------------------
    "EC-1": ("phase 6", "the refusal is tested; deriving clauses from an eval suite and "
                        "the pattern defaults is US-9's second bullet and is not built"),
    "EC-5": ("phase 5", "the acceptance rate against its band, which needs decided "
                        "proposals to have a rate at all"),
    "EC-8": ("phase 5", "the citation rule is tested; the evidence-expired state belongs "
                        "to the proposal lifecycle"),
    "EC-10": ("phase 5", "failing closed when the model provider is unavailable. Nothing "
                         "in phases 1 to 3 calls a model"),
    "EC-11": ("phase 6", "the discipline is tested; a spec-versus-ticket conflict needs a "
                         "ticket source"),
    "EC-12": ("phase 5", "a permanently easy threshold is a proposal to raise the bar, "
                         "which needs the generators"),
    # -- user stories ------------------------------------------------------------
    "US-1": ("phase 5", "the weekly harvest, its clustering, ranking and cap. The whole "
                        "story is the write half"),
    "US-2": ("phase 5", "comparing a run against its clause is tested through drift; "
                        "proposing a threshold change or a ticket needs the generators"),
    "US-10": ("phase 5", "the reviewer's story. Needs accept and reject, which are "
                         "human-only and have no surface yet"),
    "US-12": ("phase 5", "every pull request the Layer opens. It opens none yet"),
}


def _spec() -> str:
    if not SPEC.exists():  # pragma: no cover - the spec is committed
        pytest.skip("specification not present")
    return SPEC.read_text()


def refs_in_spec(pattern: str) -> list[str]:
    found = {m for m in re.findall(pattern, _spec())}
    return sorted(found, key=lambda r: int(re.search(r"\d+", r).group()))


def story_criteria() -> dict[str, list[str]]:
    """Each user story's `shall` bullets, bounded at the next heading.

    Bounded on purpose: an unbounded split ran past US-13 into the non-goals list that
    follows it and counted five of those as story criteria, which would have made the
    denominator wrong in the direction that looks like more work rather than less.
    """
    text = _spec()
    out: dict[str, list[str]] = {}
    blocks = re.split(r"^### (US-\d+)[^\n]*$", text, flags=re.M)
    for index in range(1, len(blocks), 2):
        ref, body = blocks[index], blocks[index + 1]
        body = re.split(r"^#{2,3} ", body, maxsplit=1, flags=re.M)[0]
        bullets = re.findall(r"^- (.+?)(?=\n(?:- |\n|$))", body, re.M | re.S)
        out[ref] = [re.sub(r"\s+", " ", b).strip() for b in bullets]
    return out


def markers() -> dict[str, set[str]]:
    """Every ref a test claims, by marker kind, read from the source.

    From the source rather than from pytest's own collection, so the matrix can be read
    and reasoned about without a database: a marker on a test that needs Postgres still
    counts as a claim, and whether it passes is the suite's job to say.
    """
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted(TESTS.glob("test_*.py")):
        for kind, ref in re.findall(
            r'mark\.(ac|hard_case|edge_case|story)\("([^"]+)"\)', path.read_text()
        ):
            out[kind].add(ref)
    return out


def _missing(refs: list[str], claimed: set[str]) -> list[str]:
    return [r for r in refs if r not in claimed and r not in DEFERRED]


# -- the guards on the matrix itself ---------------------------------------------


def test_the_specification_is_readable_and_holds_what_is_expected():
    """A guard on the guard. Every assertion below is vacuous if the parse returns
    nothing, and a silently empty parse is how a matrix comes to report full coverage."""
    assert len(refs_in_spec(r"\bAC-\d+\b")) == 39
    assert len(refs_in_spec(r"\bH\d+\b")) == 16
    assert len(refs_in_spec(r"\bEC-\d+\b")) == 12
    stories = story_criteria()
    assert len(stories) == 13
    assert all(stories.values()), [k for k, v in stories.items() if not v]
    # US-13 has six criteria. Seven would mean the parse ran into the non-goals list.
    assert len(stories["US-13"]) == 6, stories["US-13"]


def test_no_deferral_names_something_already_covered():
    """A deferral that is wrong in this direction is worse than a missing test: it
    records an untruth about the build and nothing would ever contradict it."""
    claimed = set().union(*markers().values()) if markers() else set()
    stale = sorted(ref for ref in DEFERRED if ref in claimed)
    # A partially covered case may legitimately carry both, so the check is that the
    # reason says so rather than that the marker is absent.
    for ref in stale:
        phase, reason = DEFERRED[ref]
        assert "is tested" in reason, (
            f"{ref} is both deferred and claimed by a test, and its reason does not say "
            f"which half is which: {reason!r}"
        )


def test_every_deferral_names_a_phase_and_a_reason():
    for ref, (phase, reason) in DEFERRED.items():
        assert phase.startswith("phase "), (ref, phase)
        assert len(reason) > 30, (ref, reason)


# -- the three criteria ----------------------------------------------------------


@pytest.mark.ac("AC-10")
def test_every_hard_case_has_a_test():
    """AC-10: "Every H1 to H16 case has a test, and each passes. H13 to H16 were added
    after this criterion was first written and are covered by it"."""
    missing = _missing(refs_in_spec(r"\bH\d+\b"), markers()["hard_case"])
    assert missing == [], f"hard cases with no test and no declared deferral: {missing}"


@pytest.mark.ac("AC-12")
def test_every_edge_case_has_a_test():
    """AC-12: "Every EC-1 to EC-12 edge case has a defined behaviour and a test"."""
    missing = _missing(refs_in_spec(r"\bEC-\d+\b"), markers()["edge_case"])
    assert missing == [], f"edge cases with no test and no declared deferral: {missing}"


@pytest.mark.ac("AC-11")
def test_every_user_story_has_a_test():
    """AC-11: "Every US-1 to US-13 acceptance criterion has a test, and each passes".

    Per story rather than per bullet at this level; the per-criterion count is the next
    test, which is the one that makes the claim specific.
    """
    missing = _missing(sorted(story_criteria(), key=lambda r: int(r.split("-")[1])),
                       markers()["story"])
    assert missing == [], f"stories with no test and no declared deferral: {missing}"


@pytest.mark.ac("AC-11")
def test_every_story_criterion_is_claimed_by_a_test():
    """The specific half of AC-11: a story with four criteria needs four tests, not one.

    Counted by how many tests carry that story's marker, which is why `test_stories.py`
    names one test per criterion rather than one per story.
    """
    counts: dict[str, int] = defaultdict(int)
    for path in sorted(TESTS.glob("test_*.py")):
        for ref in re.findall(r'mark\.story\("([^"]+)"\)', path.read_text()):
            counts[ref] += 1

    short = {}
    for ref, criteria in story_criteria().items():
        if ref in DEFERRED:
            continue
        if counts[ref] < len(criteria):
            short[ref] = f"{counts[ref]} test(s) for {len(criteria)} criteria"
    assert short == {}, f"stories covered by too few tests: {short}"


@pytest.mark.ac("AC-12")
def test_every_acceptance_criterion_has_a_test():
    """Not one of AC-10 to AC-12 by name, and the one the other three are useless
    without: a matrix that checked the hard cases and not the criteria would have missed
    AC-37, AC-38 and AC-39 entirely, which is exactly what happened."""
    missing = _missing(refs_in_spec(r"\bAC-\d+\b"), markers()["ac"])
    assert missing == [], f"criteria with no test and no declared deferral: {missing}"


# -- the matrix, rendered --------------------------------------------------------


def test_the_matrix_can_be_rendered():
    """The table that goes in PROGRESS. Printed with `-s`, asserted here so that the
    renderer cannot rot silently between the times somebody wants to read it."""
    rendered = render()
    assert "AC-1 " in rendered or "AC-1\t" in rendered
    assert "DEFERRED" in rendered
    print("\n" + rendered)


def render() -> str:
    """One line per ref: its kind, whether a test claims it, and any deferral."""
    claimed = markers()
    lines = ["ref\tkind\tstatus\tnote"]
    groups = (
        ("acceptance criterion", refs_in_spec(r"\bAC-\d+\b"), claimed["ac"]),
        ("hard case", refs_in_spec(r"\bH\d+\b"), claimed["hard_case"]),
        ("edge case", refs_in_spec(r"\bEC-\d+\b"), claimed["edge_case"]),
        ("user story", sorted(story_criteria(), key=lambda r: int(r.split("-")[1])),
         claimed["story"]),
    )
    for kind, refs, have in groups:
        for ref in refs:
            if ref in have and ref not in DEFERRED:
                status, note = "tested", ""
            elif ref in have:
                status, note = "partly tested", f"DEFERRED {DEFERRED[ref][0]}: {DEFERRED[ref][1]}"
            elif ref in DEFERRED:
                status, note = "deferred", f"DEFERRED {DEFERRED[ref][0]}: {DEFERRED[ref][1]}"
            else:
                status, note = "NO TEST", ""
            lines.append(f"{ref}\t{kind}\t{status}\t{note}")
    return "\n".join(lines)
