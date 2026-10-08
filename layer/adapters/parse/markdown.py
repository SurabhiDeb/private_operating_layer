"""Reading a markdown document into sections, tables and lines.

Deterministic and dependency-free. It knows about headings, pipe tables, list items and
line numbers, and nothing about what any of them mean — interpretation happens in the
spec adapter, which is where a product's conventions are allowed to matter.

**Line numbers are carried everywhere.** A citation that resolves to a file is weaker
than one that resolves to the lines that said it, and PRD B2's identity requirement means
a clause has to be traceable back to the text it came from after the statement has been
reworded. Every section, row and line below knows where it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
#: `## 11. Success metrics` — a leading number is how specs usually carry section refs.
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Row:
    """One body row of a pipe table, keyed by its header cells."""

    cells: dict[str, str]
    line: int

    def get(self, *names: str, default: str = "") -> str:
        """The first cell whose header matches any of `names`, case-insensitively.

        Several names because a header is prose: Target, Bar, Threshold and Value all
        mean the same column, and which one a given author used is not worth a code
        change.
        """
        lowered = {k.strip().lower(): v for k, v in self.cells.items()}
        for name in names:
            if name.strip().lower() in lowered:
                return lowered[name.strip().lower()]
        return default


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[Row]
    line: int

    def has_any(self, *names: str) -> bool:
        lowered = {h.strip().lower() for h in self.headers}
        return any(n.strip().lower() in lowered for n in names)


@dataclass
class Section:
    """A heading and everything under it, up to the next heading of the same depth."""

    ref: str | None
    title: str
    level: int
    start_line: int
    end_line: int
    lines: list[tuple[int, str]] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(text for _, text in self.lines)

    @property
    def locator(self) -> str:
        return f"L{self.start_line}-L{self.end_line}"

    def list_items(self) -> list[tuple[int, str]]:
        out = []
        for line_no, text in self.lines:
            match = _LIST_ITEM.match(text)
            if match and match.group(1).strip():
                out.append((line_no, match.group(1).strip()))
        return out

    def paragraph_lines(self) -> list[tuple[int, str]]:
        """Non-empty lines that are not tables, list items or headings."""
        out = []
        for line_no, text in self.lines:
            stripped = text.strip()
            if not stripped or stripped.startswith(("|", "#", ">")):
                continue
            if _LIST_ITEM.match(text) or _TABLE_DIVIDER.match(text):
                continue
            out.append((line_no, stripped))
        return out


def parse(markdown: str) -> list[Section]:
    """Sections in document order.

    Content before the first heading becomes a preamble section with no ref, so a
    document that states a threshold in its opening paragraph is not silently skipped.
    """
    lines = markdown.splitlines()
    sections: list[Section] = []
    current = Section(ref=None, title="", level=0, start_line=1, end_line=1)
    #: Numbered heading refs by depth, so a subsection can inherit one.
    numbered: dict[int, str] = {}
    in_fence = False

    for index, raw in enumerate(lines, start=1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            current.lines.append((index, raw))
            continue

        heading = None if in_fence else _HEADING.match(raw)
        if heading:
            current.end_line = max(current.start_line, index - 1)
            if current.lines or current.title:
                sections.append(current)
            hashes, title = heading.groups()
            level = len(hashes)
            ref, clean = _split_ref(title)
            if ref is None:
                # An unnumbered subsection belongs to its nearest numbered ancestor. A
                # metrics table under `### Retrieval` inside `## 8. Success metrics` is
                # still section 8's promise, and a ref built from the subsection's title
                # would move the moment someone renamed the heading.
                ref = _inherited_ref(numbered, level)
            else:
                numbered[level] = ref
            numbered = {lvl: r for lvl, r in numbered.items() if lvl <= level}
            current = Section(
                ref=ref, title=clean, level=level, start_line=index, end_line=index
            )
            continue

        current.lines.append((index, raw))

    current.end_line = max(current.start_line, len(lines))
    if current.lines or current.title:
        sections.append(current)

    for section in sections:
        section.tables = _tables_in(section.lines)
    return sections


def _split_ref(title: str) -> tuple[str | None, str]:
    match = _NUMBERED.match(title.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, title.strip()


def _tables_in(lines: list[tuple[int, str]]) -> list[Table]:
    """Pipe tables, found by the divider row rather than by the pipes.

    A line of pipes alone is ambiguous — prose and code contain them — but a divider
    under a header is unmistakable, and it also tells us where the header is.
    """
    tables: list[Table] = []
    # Fenced blocks are skipped here as well as in `parse`: a code sample containing
    # pipes and dashes is indistinguishable from a table once the fence is forgotten.
    lines = _outside_fences(lines)
    index = 0
    while index < len(lines):
        line_no, text = lines[index]
        if "|" in text and index + 1 < len(lines) and _TABLE_DIVIDER.match(lines[index + 1][1]):
            headers = _cells(text)
            rows: list[Row] = []
            cursor = index + 2
            while cursor < len(lines) and "|" in lines[cursor][1]:
                row_no, row_text = lines[cursor]
                cells = _cells(row_text)
                if cells and any(c for c in cells):
                    padded = cells + [""] * (len(headers) - len(cells))
                    rows.append(Row(cells=dict(zip(headers, padded)), line=row_no))
                cursor += 1
            tables.append(Table(headers=headers, rows=rows, line=line_no))
            index = cursor
            continue
        index += 1
    return tables


def _cells(text: str) -> list[str]:
    stripped = text.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _inherited_ref(numbered: dict[int, str], level: int) -> str | None:
    """The nearest numbered ancestor's ref, or None at the top of a document."""
    for depth in sorted((d for d in numbered if d < level), reverse=True):
        return numbered[depth]
    return None


def _outside_fences(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    out, in_fence = [], False
    for line_no, text in lines:
        if _FENCE.match(text):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((line_no, text))
    return out
