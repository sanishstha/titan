"""Section parser round-trips, stable ids, and absent sections."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from titan.ingest.canonical_text import CANONICAL_TEXT_VERSION
from titan.ingest.errors import FilingSectionError, SecIdentityError
from titan.ingest.sections import ParsedFiling, SectionKind, parse_filing_sections

_FIXTURES = Path(__file__).parent / "fixtures" / "sections"
_ACC = "0001045810-24-000012"
_LONG = (
    "Industrial sensors are manufactured, sold, and serviced for customers in many countries. "
)


def _read(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _assert_round_trip(parsed: ParsedFiling) -> None:
    canonical = parsed.canonical
    assert canonical.version == CANONICAL_TEXT_VERSION
    previous_end = 0
    seen: set[SectionKind] = set()
    for section in parsed.sections:
        assert section.kind not in seen
        seen.add(section.kind)
        assert 0 <= section.start < section.end <= len(canonical.text)
        assert section.start >= previous_end
        previous_end = section.end
        assert section.text == canonical.text[section.start : section.end]
        assert section.heading != ""
        assert section.text.startswith(section.heading)
        assert section.section_id.endswith(":" + section.kind.value)


@pytest.mark.parametrize(
    ("name", "form_type", "kinds"),
    [
        (
            "10k-repeated-headings.htm",
            "10-K",
            (
                SectionKind.BUSINESS,
                SectionKind.RISK_FACTORS,
                SectionKind.MDA,
                SectionKind.NOTES,
            ),
        ),
        (
            "10q-entities-unicode.htm",
            "10-Q",
            (SectionKind.NOTES, SectionKind.MDA, SectionKind.RISK_FACTORS),
        ),
        ("8k-absent.htm", "8-K", ()),
        (
            "malformed.htm",
            "10-K",
            (
                SectionKind.BUSINESS,
                SectionKind.RISK_FACTORS,
                SectionKind.MDA,
                SectionKind.NOTES,
            ),
        ),
        (
            "10k-plain-repeated.txt",
            "10-K",
            (
                SectionKind.BUSINESS,
                SectionKind.RISK_FACTORS,
                SectionKind.MDA,
                SectionKind.NOTES,
            ),
        ),
        ("cp1252-mda.htm", "10-K", (SectionKind.MDA,)),
        ("note1-without-heading.htm", "10-K", ()),
        (
            "toc-absent-risk.htm",
            "10-K",
            (SectionKind.BUSINESS, SectionKind.MDA),
        ),
    ],
)
def test_fixture_sections_round_trip(
    name: str,
    form_type: str,
    kinds: tuple[SectionKind, ...],
) -> None:
    raw = _read(name)
    parsed = parse_filing_sections(raw, form_type=form_type, accession_number=_ACC)
    assert parsed.raw_bytes is raw
    assert parsed.form_type == form_type
    _assert_round_trip(parsed)
    assert tuple(section.kind for section in parsed.sections) == kinds
    again = parse_filing_sections(raw, form_type=form_type, accession_number=_ACC)
    assert again == parsed


def test_repeated_html_headings_skip_toc_and_keep_one_span() -> None:
    raw = _read("10k-repeated-headings.htm")
    parsed = parse_filing_sections(raw, form_type="10-K", accession_number=_ACC)
    text = parsed.canonical.text
    sections = {section.kind: section for section in parsed.sections}
    business = sections[SectionKind.BUSINESS]
    risk = sections[SectionKind.RISK_FACTORS]
    mda = sections[SectionKind.MDA]
    notes = sections[SectionKind.NOTES]

    assert "HIDDEN_BUSINESS_MARKER" not in text
    assert "HIDDEN_IX_MARKER" not in text
    assert "COMMENT_SHOULD_NOT_APPEAR" not in text
    assert "var toc" not in text
    assert "....." not in business.heading
    assert business.start > text.index("Item 1. Business")
    assert business.heading == "Item 1.\nBusiness"
    assert "NVIDIA designs GPUs." in business.text
    assert "See Item 1A. Risk Factors for the risks" in business.text
    assert "Continued discussion of the GPU business" in business.text
    assert "PART I" not in business.text
    assert "We face competition" not in business.text

    assert risk.heading == "Item 1A. Risk Factors"
    assert "We face competition" in risk.text
    assert "Unresolved Staff Comments" not in risk.text
    assert "Gross margin changed" in mda.text
    assert "Interest rate risk is limited." not in mda.text
    assert notes.heading == "Notes to Consolidated Financial Statements"
    assert "independent auditors" not in notes.text
    assert "Note 2. Revenue recognition." in notes.text
    assert "Item 9." not in notes.text
    assert notes.section_id == f"{CANONICAL_TEXT_VERSION}:{_ACC}:notes"


def test_plain_repeated_headings_and_cross_reference() -> None:
    parsed = parse_filing_sections(
        _read("10k-plain-repeated.txt"),
        form_type="10-K",
        accession_number=_ACC,
    )
    sections = {section.kind: section for section in parsed.sections}
    business = sections[SectionKind.BUSINESS]
    assert "....." not in business.heading
    assert business.start > parsed.canonical.text.index("Item 1. Business")
    assert _LONG.strip() in business.text
    assert "See Item 1A. Risk Factors." in business.text
    assert "this line is prose" in business.text
    assert "Continued discussion after a repeated plain heading." in business.text
    assert "Customers are concentrated" not in business.text
    assert "Interest rate risk is limited." not in sections[SectionKind.MDA].text
    assert "The audit report" not in sections[SectionKind.NOTES].text
    assert "café" in sections[SectionKind.NOTES].text
    assert "Item 9." not in sections[SectionKind.NOTES].text


def test_unicode_entities_and_absent_business_on_10q() -> None:
    raw = _read("10q-entities-unicode.htm")
    parsed = parse_filing_sections(raw, form_type="10-Q", accession_number=_ACC)
    text = parsed.canonical.text
    assert b"Cafe\xcc\x81" in raw
    assert "\u0301" not in text
    assert "&amp;" not in text
    assert "\u00a0" not in text
    assert "Café & Co." in text
    assert "€12" in text
    assert "¥1" in text
    assert "東京" in text
    sections = {section.kind: section for section in parsed.sections}
    assert SectionKind.BUSINESS not in sections
    notes = sections[SectionKind.NOTES]
    mda = sections[SectionKind.MDA]
    risk = sections[SectionKind.RISK_FACTORS]
    assert "Café & Co." in notes.text
    assert "€12" in notes.text
    assert "¥1" in notes.text
    assert "東京" in notes.text
    assert "non-breaking space sits in this note." in notes.text
    assert "Revenue grew" not in notes.text
    assert mda.heading.startswith("Item 2. Management\u2019s Discussion")
    assert mda.start > text.index("Item 2. Management")
    assert "Revenue grew. The café added capacity." in mda.text
    assert "Supply of beans" not in mda.text
    assert "Part II" not in mda.text
    assert "Supply of beans is concentrated." in risk.text
    assert "Unregistered Sales" not in risk.text
    assert parsed.canonical.encoding == "utf-8"


def test_8k_does_not_invent_sections() -> None:
    parsed = parse_filing_sections(_read("8k-absent.htm"), form_type="8-K", accession_number=_ACC)
    assert parsed.sections == ()
    assert "Item 2.02" in parsed.canonical.text
    assert "Results of Operations and Financial Condition" in parsed.canonical.text
    assert "Exhibit 99.1" in parsed.canonical.text
    assert "management's discussion appears in this sentence" in parsed.canonical.text


def test_malformed_html_still_slices_canonical_text() -> None:
    parsed = parse_filing_sections(
        _read("malformed.htm"),
        form_type="10-K/A",
        accession_number=_ACC,
    )
    sections = {section.kind: section for section in parsed.sections}
    assert "widgets & gadgets." in sections[SectionKind.BUSINESS].text
    assert "nested bold" in sections[SectionKind.RISK_FACTORS].text
    assert "Margins moved." in sections[SectionKind.MDA].text
    assert "significant accounting policies." in sections[SectionKind.NOTES].text
    _assert_round_trip(parsed)


def test_note_number_without_collective_heading_is_absent() -> None:
    parsed = parse_filing_sections(
        _read("note1-without-heading.htm"),
        form_type="10-Q/A",
        accession_number=_ACC,
    )
    assert parsed.sections == ()
    assert "Note 1. Organization" in parsed.canonical.text
    assert "Note 2. Revenue" in parsed.canonical.text


def test_toc_mention_without_a_body_is_not_a_section() -> None:
    parsed = parse_filing_sections(
        _read("toc-absent-risk.htm"),
        form_type="10-K",
        accession_number=_ACC,
    )
    assert SectionKind.RISK_FACTORS not in {section.kind for section in parsed.sections}
    business = next(section for section in parsed.sections if section.kind is SectionKind.BUSINESS)
    assert business.start > parsed.canonical.text.index("Item 1. Business")
    assert _LONG.strip() in business.text


def test_cp1252_smart_quote_decodes_into_the_section() -> None:
    raw = _read("cp1252-mda.htm")
    assert b"\x92" in raw
    parsed = parse_filing_sections(raw, form_type="10-K", accession_number=_ACC)
    assert parsed.canonical.encoding == "cp1252"
    mda = parsed.sections[0]
    assert mda.kind is SectionKind.MDA
    assert "caf\u2019s margin held." in mda.text
    assert mda.text == parsed.canonical.text[mda.start : mda.end]


def test_section_id_uses_accession_or_raw_hash() -> None:
    raw = _read("8k-absent.htm")
    # An empty section list still parses; use the 10-K so an id exists.
    body = _read("cp1252-mda.htm")
    parsed = parse_filing_sections(body, form_type="8-K/A", accession_number=_ACC)
    assert parsed.sections[0].section_id == f"{CANONICAL_TEXT_VERSION}:{_ACC}:mda"
    hashed = parse_filing_sections(body, form_type="10-K")
    digest = hashlib.sha256(body).hexdigest()
    assert hashed.sections[0].section_id == f"{CANONICAL_TEXT_VERSION}:{digest}:mda"
    assert parse_filing_sections(body, form_type="10-K").sections[0].section_id == (
        hashed.sections[0].section_id
    )
    other = parse_filing_sections(raw, form_type="8-K")
    assert other.sections == ()
    assert other.raw_bytes is raw


def test_prose_and_short_mda_alias_do_not_invent_business() -> None:
    prose = (
        b"<html><body><p>Item 1. Business we operate in many markets and this "
        b"sentence must not become the business section.</p>"
        b"<p><b>Item 7. MD&amp;A</b></p><p>Margins held.</p></body></html>"
    )
    parsed = parse_filing_sections(prose, form_type="10-K")
    assert [section.kind for section in parsed.sections] == [SectionKind.MDA]
    assert parsed.sections[0].heading == "Item 7. MD&A"
    assert "Margins held." in parsed.sections[0].text
    assert "we operate" not in parsed.sections[0].text


def test_split_heading_with_br_and_bare_risk_factors() -> None:
    raw = (
        b"<html><body><p><b>Item 1.<br>Business</b></p>"
        b"<p>The registrant designs equipment.</p>"
        b"<p><b>Risk Factors</b></p><p>Supply is concentrated.</p></body></html>"
    )
    parsed = parse_filing_sections(raw, form_type="10-Q")
    assert [section.kind for section in parsed.sections] == [
        SectionKind.BUSINESS,
        SectionKind.RISK_FACTORS,
    ]
    assert parsed.sections[0].heading == "Item 1.\nBusiness"
    assert parsed.sections[1].heading == "Risk Factors"
    _assert_round_trip(parsed)


def test_utf8_bom_is_kept_in_the_raw_bytes() -> None:
    body = _read("8k-absent.htm")
    raw = b"\xef\xbb\xbf" + body
    parsed = parse_filing_sections(raw, form_type="8-K")
    assert parsed.raw_bytes is raw
    assert parsed.canonical.encoding == "utf-8"
    assert parsed.sections == ()
    assert "Item 2.02" in parsed.canonical.text


def test_rejects_unknown_form_bad_accession_and_non_bytes() -> None:
    raw = b"<html><body><p>Item 1. Business</p></body></html>"
    with pytest.raises(FilingSectionError):
        parse_filing_sections(raw, form_type="S-1")
    with pytest.raises(SecIdentityError):
        parse_filing_sections(raw, form_type="10-K", accession_number="not-an-accession")
    with pytest.raises(FilingSectionError):
        parse_filing_sections("not-bytes", form_type="8-K")  # type: ignore[arg-type]
