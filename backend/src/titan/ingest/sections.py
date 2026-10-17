"""Deterministic filing sections for 10-K, 10-Q, and 8-K documents.

The parser looks for four headings: Business, Risk Factors, MD&A, and Notes
to Financial Statements. A heading is emitted only when that line is present.
A 10-Q Item 1 Financial Statements is not Business. An 8-K Item 2.02 Results
of Operations is not MD&A. A run of ``Note 1`` headings without a collective
notes title is not a notes section.

Offsets are Python character indexes into ``sec-filing-text-v1`` canonical
text. ``section.text`` is ``canonical.text[start:end]``. The original bytes
stay on the result unchanged. Section ids are
``{version}:{accession-or-sha256}:{kind}`` and do not depend on offsets.

Table-of-contents lines are rejected when they are hyperlinks, dot leaders,
or a trailing page number, when the same heading is repeated later with a
real body, or when a dense run of short plain headings sits before the first
long section. A later repeat of the same item in the same part does not split that
section. The same item number after a new part heading is a new boundary,
so a 10-Q Part II Item 2 does not continue Part I Item 2. No embedding and
no model is used.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from titan.ingest.canonical_text import (
    CANONICAL_TEXT_VERSION,
    CanonicalFilingText,
    CanonicalLine,
    to_canonical_text,
)
from titan.ingest.errors import FilingSectionError
from titan.ingest.identifiers import validate_accession
from titan.ingest.submissions import TARGET_FORMS

# Alphabetic characters, not heading length. A table-of-contents entry has
# almost none before the next entry. A real section that anchors the front
# matter has a long body. Repeats of the same item after that anchor stay in
# the section instead of starting a new one.
_TOC_CLUSTER_ALPHA = 15
_TOC_CLUSTER_MIN = 3
_TOC_SHORT_ALPHA = 40
_TOC_LATER_ALPHA = 80
_TOC_ANCHOR_ALPHA = 300
_MAX_TITLE_WORDS = 18
_MAX_TITLE_CHARS = 160

_PUNCT = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u2032": "'",
        "`": "'",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)
_PROSE_WORDS = frozenset(
    {
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "may",
        "might",
        "will",
        "shall",
        "can",
        "could",
        "should",
        "would",
        "not",
        "we",
        "our",
        "its",
        "their",
        "this",
        "that",
        "these",
        "those",
    }
)
_DOT_LEADER = re.compile(r"(?:\.\s*){2,}|…+|·{2,}")
_PAGE_SUFFIX = re.compile(r"^(?P<body>.*?)\s+\d{1,4}$")
_HEADING_START = re.compile(
    r"^(?:item\s+\d|part\s+[ivx]+|notes\s+to\b|risk\s+factors$|management's\s+discussion|md&a$)"
)
_NOTES_RE = re.compile(
    r"notes to (?:the\s+)?(?:(?:unaudited|condensed|consolidated|and combined)\s+)*"
    r"financial statements(?:\s*\((?:unaudited|continued)\))?$"
)
_PART_RE = re.compile(r"part\s+(?P<roman>i{1,3}|iv|vi{0,3}|ix|x)$")
_SIGNATURE_RE = re.compile(r"signatures?$")
_MDA_RE = re.compile(
    r"(?:md&a|management's discussion(?: and analysis)?"
    r"(?: of financial condition and results of operations)?)$"
)
_ITEM_ONLY_RE = re.compile(r"item\s+(?P<num>\d{1,2})(?P<suffix>[a-c])?(?:\s*[-.:)])?$")
_ITEM_RE = re.compile(
    r"item\s+(?P<num>\d{1,2})(?P<suffix>[a-c])?(?:\s*[-.:)]\s*|\s+)(?P<title>.+)$"
)


class SectionKind(StrEnum):
    """One citeable narrative section."""

    BUSINESS = "business"
    RISK_FACTORS = "risk_factors"
    MDA = "mda"
    NOTES = "notes"


@dataclass(frozen=True)
class FilingSection:
    """A section whose text is a slice of canonical filing text."""

    section_id: str
    kind: SectionKind
    heading: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ParsedFiling:
    """Original bytes, the canonical text they became, and any real sections."""

    raw_bytes: bytes
    form_type: str
    canonical: CanonicalFilingText
    sections: tuple[FilingSection, ...]


@dataclass(frozen=True)
class _Classified:
    identity: tuple[str, ...]
    kind: SectionKind | None
    toc: bool
    item_only: bool
    num: str
    suffix: str


@dataclass(frozen=True)
class _Heading:
    start: int
    line_index: int
    title_line_index: int
    identity: tuple[str, ...]
    kind: SectionKind | None
    toc: bool
    bold: bool
    heading_tag: bool


def parse_filing_sections(
    raw: bytes,
    *,
    form_type: str,
    accession_number: str | None = None,
) -> ParsedFiling:
    """Parse sections from one primary filing.

    ``form_type`` must be a 10-K, 10-Q, 8-K, or ``/A`` amendment. It is
    recorded and checked. Headings are recognized from their own text, so an
    item number alone never selects Business or MD&A. Pass the accession when
    the section id should follow the document; otherwise the id uses the
    SHA-256 of ``raw``.
    """
    if not isinstance(raw, bytes):
        raise FilingSectionError("filing body must be bytes")
    if form_type not in TARGET_FORMS:
        raise FilingSectionError("form type must be a 10-K, 10-Q, 8-K, or an amendment")
    if accession_number is None:
        identity = hashlib.sha256(raw).hexdigest()
    else:
        identity = validate_accession(accession_number)
    canonical, lines = to_canonical_text(raw)
    found = _find_sections(canonical.text, lines, identity)
    return ParsedFiling(
        raw_bytes=raw,
        form_type=form_type,
        canonical=canonical,
        sections=found,
    )


def _find_sections(
    text: str,
    lines: Sequence[CanonicalLine],
    identity: str,
) -> tuple[FilingSection, ...]:
    headings = _reject_toc(_collect_headings(lines), lines, text)
    kept: list[_Heading] = []
    seen_identity: set[tuple[str, ...]] = set()
    seen_kind: set[SectionKind] = set()
    for heading in headings:
        if heading.toc or heading.identity in seen_identity:
            continue
        if heading.kind is not None and heading.kind in seen_kind:
            continue
        seen_identity.add(heading.identity)
        if heading.kind is not None:
            seen_kind.add(heading.kind)
        kept.append(heading)
    sections: list[FilingSection] = []
    for index, heading in enumerate(kept):
        if heading.kind is None:
            continue
        end = kept[index + 1].start if index + 1 < len(kept) else len(text)
        section_text = text[heading.start:end]
        heading_text = _heading_text(text, lines, heading)
        if section_text == "" or not section_text.startswith(heading_text):
            raise FilingSectionError("section span does not match canonical text")
        sections.append(
            FilingSection(
                section_id=f"{CANONICAL_TEXT_VERSION}:{identity}:{heading.kind.value}",
                kind=heading.kind,
                heading=heading_text,
                start=heading.start,
                end=end,
                text=section_text,
            )
        )
    return tuple(sections)


def _collect_headings(lines: Sequence[CanonicalLine]) -> list[_Heading]:
    found: list[_Heading] = []
    index = 0
    current_part = ""
    while index < len(lines):
        classified = _classify_line(lines[index].text, toc_link=lines[index].toc_link)
        if classified is None:
            index += 1
            continue
        title_index = index
        chosen = classified
        if classified.item_only and index + 1 < len(lines):
            merged = _merge_item_title(classified, lines[index + 1])
            if merged is not None:
                chosen = merged
                title_index = index + 1
        if chosen.identity[0] == "part":
            current_part = chosen.identity[1]
        else:
            chosen = _with_part(chosen, current_part)
        if chosen.item_only:
            index += 1
            continue
        found.append(
            _Heading(
                start=lines[index].start,
                line_index=index,
                title_line_index=title_index,
                identity=chosen.identity,
                kind=chosen.kind,
                toc=chosen.toc,
                bold=lines[index].bold or lines[title_index].bold,
                heading_tag=lines[index].heading_tag or lines[title_index].heading_tag,
            )
        )
        index = title_index + 1
    return found


def _with_part(classified: _Classified, part: str) -> _Classified:
    if classified.identity[0] != "item":
        return classified
    return _Classified(
        identity=("item", part, classified.num, classified.suffix),
        kind=classified.kind,
        toc=classified.toc,
        item_only=classified.item_only,
        num=classified.num,
        suffix=classified.suffix,
    )


def _merge_item_title(item: _Classified, following: CanonicalLine) -> _Classified | None:
    title, decorated = _prepare_key(following.text)
    if title == "" or _ITEM_ONLY_RE.fullmatch(title) or _ITEM_RE.fullmatch(title):
        return None
    if not _is_short_title(title):
        return None
    kind = _kind_for_item(item.num, item.suffix, title)
    if kind is None and not _is_caption(title):
        return None
    return _Classified(
        identity=("item", item.num, item.suffix),
        kind=kind,
        toc=item.toc or following.toc_link or decorated,
        item_only=False,
        num=item.num,
        suffix=item.suffix,
    )


def _classify_line(text: str, *, toc_link: bool) -> _Classified | None:
    key, decorated = _prepare_key(text)
    if key == "":
        return None
    toc = toc_link or decorated
    if _NOTES_RE.fullmatch(key):
        return _Classified(("notes",), SectionKind.NOTES, toc, False, "", "")
    part = _PART_RE.fullmatch(key)
    if part is not None:
        return _Classified(("part", part.group("roman")), None, toc, False, "", "")
    if _SIGNATURE_RE.fullmatch(key):
        return _Classified(("signature",), None, toc, False, "", "")
    if key == "risk factors":
        return _Classified(("item", "1", "a"), SectionKind.RISK_FACTORS, toc, False, "1", "a")
    if _MDA_RE.fullmatch(key):
        return _Classified(("mda",), SectionKind.MDA, toc, False, "", "")
    item_only = _ITEM_ONLY_RE.fullmatch(key)
    if item_only is not None:
        return _item_only(item_only, toc=toc)
    item = _ITEM_RE.fullmatch(key)
    if item is None:
        return None
    return _classify_item(item, toc=toc)


def _item_only(match: re.Match[str], *, toc: bool) -> _Classified:
    suffix = match.group("suffix") or ""
    number = match.group("num")
    return _Classified(("item", number, suffix), None, toc, True, number, suffix)


def _classify_item(match: re.Match[str], *, toc: bool) -> _Classified | None:
    number = match.group("num")
    suffix = match.group("suffix") or ""
    title = match.group("title").strip(" .;:-")
    if title == "":
        return _Classified(("item", number, suffix), None, toc, True, number, suffix)
    kind = _kind_for_item(number, suffix, title)
    if not _is_short_title(title):
        return None
    if kind is None and not _is_caption(title):
        return None
    return _Classified(("item", number, suffix), kind, toc, False, number, suffix)


def _kind_for_item(number: str, suffix: str, title: str) -> SectionKind | None:
    if number == "1" and suffix == "" and title == "business":
        return SectionKind.BUSINESS
    if number == "1" and suffix == "a" and title == "risk factors":
        return SectionKind.RISK_FACTORS
    if _MDA_RE.fullmatch(title):
        return SectionKind.MDA
    return None


def _prepare_key(text: str) -> tuple[str, bool]:
    key = re.sub(r"\s+", " ", text.translate(_PUNCT).lower()).strip()
    toc = False
    if _DOT_LEADER.search(key):
        toc = True
        key = _DOT_LEADER.sub(" ", key)
        key = re.sub(r" {2,}", " ", key).strip()
    page = _PAGE_SUFFIX.fullmatch(key)
    if page is not None and _HEADING_START.match(page.group("body").strip()):
        toc = True
        key = page.group("body").strip()
    return key.strip(" .;:-"), toc


def _is_short_title(title: str) -> bool:
    words = title.split()
    return 0 < len(words) <= _MAX_TITLE_WORDS and len(title) <= _MAX_TITLE_CHARS


def _is_caption(title: str) -> bool:
    if not _is_short_title(title):
        return False
    return all(word not in _PROSE_WORDS for word in title.split())


def _reject_toc(
    headings: list[_Heading],
    lines: Sequence[CanonicalLine],
    text: str,
) -> list[_Heading]:
    if not headings:
        return headings
    bodies = [_body_alpha(index, headings, lines, text) for index in range(len(headings))]
    first_long = next((i for i, count in enumerate(bodies) if count >= _TOC_ANCHOR_ALPHA), None)
    marked = [heading.toc for heading in headings]
    if first_long is not None:
        _mark_short_duplicates(headings, bodies, first_long, marked)
        _mark_cluster(headings, bodies, first_long, marked)
    return [
        _Heading(
            start=heading.start,
            line_index=heading.line_index,
            title_line_index=heading.title_line_index,
            identity=heading.identity,
            kind=heading.kind,
            toc=is_toc,
            bold=heading.bold,
            heading_tag=heading.heading_tag,
        )
        for heading, is_toc in zip(headings, marked, strict=True)
    ]


def _body_alpha(
    index: int,
    headings: Sequence[_Heading],
    lines: Sequence[CanonicalLine],
    text: str,
) -> int:
    title_line = lines[headings[index].title_line_index]
    content_at = title_line.start + len(title_line.text)
    identity = headings[index].identity
    end = len(text)
    for later in headings[index + 1 :]:
        if later.identity != identity:
            end = later.start
            break
    return sum(1 for char in text[content_at:end] if char.isalpha())


def _mark_short_duplicates(
    headings: Sequence[_Heading],
    bodies: Sequence[int],
    first_long: int,
    marked: list[bool],
) -> None:
    best_after: dict[tuple[str, ...], int] = {}
    for index in range(len(headings) - 1, -1, -1):
        if marked[index]:
            continue
        identity = headings[index].identity
        later = best_after.get(identity, 0)
        short_front = index < first_long and bodies[index] < _TOC_SHORT_ALPHA
        if short_front and later >= _TOC_LATER_ALPHA:
            marked[index] = True
            continue
        best_after[identity] = max(later, bodies[index])


def _mark_cluster(
    headings: Sequence[_Heading],
    bodies: Sequence[int],
    first_long: int,
    marked: list[bool],
) -> None:
    run_start = 0
    while run_start < first_long:
        if not _in_cluster(headings[run_start], bodies[run_start]):
            run_start += 1
            continue
        run_end = run_start
        while run_end < first_long and _in_cluster(headings[run_end], bodies[run_end]):
            run_end += 1
        if run_end - run_start >= _TOC_CLUSTER_MIN:
            for index in range(run_start, run_end):
                marked[index] = True
        run_start = run_end


def _in_cluster(heading: _Heading, body: int) -> bool:
    if heading.bold or heading.heading_tag:
        return False
    return body < _TOC_CLUSTER_ALPHA


def _heading_text(text: str, lines: Sequence[CanonicalLine], heading: _Heading) -> str:
    title_line = lines[heading.title_line_index]
    return text[heading.start : title_line.start + len(title_line.text)]
