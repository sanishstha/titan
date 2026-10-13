"""Application code reads documents and facts only through the repository."""

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "titan"
_REPOSITORY = "pit/repository.py"
_RAW_READS = (
    re.compile(r"\bselect\s*\(\s*(?:Fact|Document)\b"),
    re.compile(r"\bselect\s*\(\s*\n\s*(?:Fact|Document)\b"),
    re.compile(r"\.\s*(?:query|get)\s*\(\s*(?:Fact|Document)\b"),
    re.compile(
        r"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?(?:public\.)?(?:facts|documents)\b",
        re.IGNORECASE,
    ),
)


def raw_financial_read_hits(source: str) -> list[str]:
    """Return pattern names that select or join the financial tables."""
    hits: list[str] = []
    for pattern in _RAW_READS:
        if pattern.search(source):
            hits.append(pattern.pattern)
    return hits


def test_scanner_flags_a_direct_fact_or_document_read() -> None:
    assert raw_financial_read_hits("rows = select(Fact).where(Fact.known_at <= as_of)")
    assert raw_financial_read_hits("session.get(Document, document_id)")
    assert raw_financial_read_hits("SELECT value FROM facts WHERE company_id = 1")
    assert raw_financial_read_hits("SELECT * FROM documents d JOIN facts f ON f.document_id = d.id")
    assert raw_financial_read_hits("def load():\n    return select(\n        Document\n    )")
    assert raw_financial_read_hits("class Fact:\n    __tablename__ = 'facts'\n") == []


def test_multiline_select_is_flagged() -> None:
    source = "statement = select(\n    Fact\n)\n"
    assert raw_financial_read_hits(source)


def test_application_code_reads_facts_and_documents_only_in_the_repository() -> None:
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        if relative == _REPOSITORY:
            continue
        hits = raw_financial_read_hits(path.read_text(encoding="utf-8"))
        if hits:
            violations.append(f"{relative}: {hits[0]}")
    assert violations == []


def test_repository_module_is_the_financial_read_path() -> None:
    source = (_SRC / "pit" / "repository.py").read_text(encoding="utf-8")
    assert "select(Document)" in source
    assert "select(Fact)" in source
    assert "known_at <= cutoff" in source
    assert "Document.known_at <= cutoff" in source
