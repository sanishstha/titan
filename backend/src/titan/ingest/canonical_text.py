"""Versioned plain text for a filing.

``sec-filing-text-v1`` is derived from the original bytes and is the only
string section offsets index into. The bytes themselves are not rewritten.
A null byte is dropped before parsing because HTMLParser would stop at it.
``iso-8859-1`` is decoded as Windows-1252, which is how browsers read the
Latin-1 labels SEC pages use for smart quotes.

HTML becomes one line per block element, ``<br>``, table cell, or list item.
Tags, comments, ``head``, ``script``, ``style``, ``ix:header``, ``ix:hidden``,
and ``display:none`` / ``visibility:hidden`` subtrees are omitted. Entities
are decoded. Lines are Unicode NFC. Horizontal whitespace, including NBSP,
collapses to a single space. Soft hyphens and zero-width characters are
dropped. Blank lines are not kept. A newline that only pretty-prints a tag
becomes a space; a plain-text filing keeps its non-empty lines.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

CANONICAL_TEXT_VERSION = "sec-filing-text-v1"

_ENCODING_LABELS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "us-ascii": "ascii",
    "ascii": "ascii",
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "latin-1": "cp1252",
    "latin1": "cp1252",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
}
_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9._+-]+)""", re.IGNORECASE)
_XML_ENCODING_RE = re.compile(
    rb"""<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9._+-]+)["']""",
    re.IGNORECASE,
)
_HEAD_END_RE = re.compile(br"</head>", re.IGNORECASE)
_HTML_RE = re.compile(
    r"<\s*(?:!doctype|html|body|div|p|br|span|font|h[1-6]|a|td|tr|table|b)\b",
    re.IGNORECASE,
)
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_SKIP_TAGS = frozenset({"script", "style", "head", "noscript", "svg", "template"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "body",
        "br",
        "caption",
        "center",
        "dd",
        "div",
        "dt",
        "figcaption",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "html",
        "li",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "ul",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BOLD_TAGS = frozenset({"b", "strong"})
_UNICODE_SPACE = (
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u202f\u205f\u3000"
)
_DROP_CHARS = "\u00ad\u200b\u200c\u200d\ufeff"
_TRANSLATE = str.maketrans(
    {ord(char): " " for char in _UNICODE_SPACE}
    | {ord(char): None for char in _DROP_CHARS}
    | {ord(char): " " for char in "\t\n\r\f\v"}
)


@dataclass(frozen=True)
class CanonicalFilingText:
    """Plain text produced by one named canonicalization."""

    version: str
    text: str
    encoding: str


@dataclass(frozen=True)
class CanonicalLine:
    """One non-empty line and the flags heading detection uses."""

    start: int
    text: str
    toc_link: bool
    bold: bool
    heading_tag: bool


@dataclass(frozen=True)
class _Piece:
    text: str
    bold: bool
    in_link: bool


@dataclass
class _OpenTag:
    name: str
    skip: bool
    link: bool
    bold: bool
    heading: bool


@dataclass
class _Draft:
    text: str
    toc_link: bool
    bold: bool
    heading_tag: bool


def to_canonical_text(raw: bytes) -> tuple[CanonicalFilingText, tuple[CanonicalLine, ...]]:
    """Return versioned text and its lines. ``raw`` is not modified."""
    decoded, encoding = decode_filing_bytes(raw)
    if "\x00" in decoded:
        decoded = decoded.replace("\x00", "")
    if _HTML_RE.search(decoded) is not None:
        drafts = _html_lines(decoded)
    else:
        drafts = _plain_lines(decoded)
    text, lines = _position(drafts)
    canonical = CanonicalFilingText(version=CANONICAL_TEXT_VERSION, text=text, encoding=encoding)
    return canonical, lines


def decode_filing_bytes(raw: bytes) -> tuple[str, str]:
    """Decode a filing, preferring its declared charset, then UTF-8, then cp1252."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    declared = _declared_encoding(raw)
    if declared is not None:
        try:
            return raw.decode(declared), declared
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def _declared_encoding(raw: bytes) -> str | None:
    window = raw[:8192]
    head_end = _HEAD_END_RE.search(window)
    if head_end is not None:
        window = window[: head_end.end()]
    match = _CHARSET_RE.search(window)
    if match is None:
        match = _XML_ENCODING_RE.search(window)
    if match is None:
        return None
    label = match.group(1).decode("ascii", errors="ignore").lower()
    return _ENCODING_LABELS.get(label)


def _normalize_line(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).translate(_TRANSLATE)
    return re.sub(r" {2,}", " ", normalized).strip()


def _plain_lines(text: str) -> list[_Draft]:
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    drafts: list[_Draft] = []
    for raw_line in normalized.split("\n"):
        line = _normalize_line(raw_line)
        if line == "":
            continue
        drafts.append(_Draft(text=line, toc_link=False, bold=False, heading_tag=False))
    return drafts


def _html_lines(text: str) -> list[_Draft]:
    parser = _FilingHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.drafts()


def _position(drafts: list[_Draft]) -> tuple[str, tuple[CanonicalLine, ...]]:
    parts: list[str] = []
    lines: list[CanonicalLine] = []
    offset = 0
    for index, draft in enumerate(drafts):
        if index:
            parts.append("\n")
            offset += 1
        lines.append(
            CanonicalLine(
                start=offset,
                text=draft.text,
                toc_link=draft.toc_link,
                bold=draft.bold,
                heading_tag=draft.heading_tag,
            )
        )
        parts.append(draft.text)
        offset += len(draft.text)
    return "".join(parts), tuple(lines)


def _attribute(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    for key, value in attrs:
        if key == name:
            return value
    return None


class _FilingHTMLParser(HTMLParser):
    """Turn filing HTML into text lines. Malformed markup is kept as text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._open: list[_OpenTag] = []
        self._pieces: list[_Piece] = []
        self._blocks: list[tuple[list[_Piece], bool]] = []
        self._bold_depth = 0
        self._link_depth = 0
        self._heading_depth = 0
        self._block_heading = False

    def drafts(self) -> list[_Draft]:
        rendered: list[_Draft] = []
        for pieces, heading_tag in self._blocks:
            draft = _render_block(pieces, heading_tag=heading_tag)
            if draft is not None:
                rendered.append(draft)
        return rendered

    def close(self) -> None:
        super().close()
        self._flush()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag.lower(), attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        self._start(name, attrs)
        self._end(name)

    def handle_endtag(self, tag: str) -> None:
        self._end(tag.lower())

    def handle_data(self, data: str) -> None:
        if data == "" or self._skipping():
            return
        self._pieces.append(
            _Piece(
                text=data,
                bold=self._bold_depth > 0 or self._heading_depth > 0,
                in_link=self._link_depth > 0,
            )
        )

    def _start(self, name: str, attrs: list[tuple[str, str | None]]) -> None:
        already_skipping = self._skipping()
        skip = (not already_skipping) and _should_skip(name, attrs)
        local = name.split(":")[-1]
        link = name == "a" and _has_href(attrs)
        bold = local in _BOLD_TAGS or name in _BOLD_TAGS
        heading = local in _HEADING_TAGS or name in _HEADING_TAGS
        block = local in _BLOCK_TAGS or name in _BLOCK_TAGS
        if not already_skipping and (block or skip):
            self._flush()
        self._open.append(_OpenTag(name=name, skip=skip, link=link, bold=bold, heading=heading))
        if already_skipping or skip:
            return
        if link:
            self._link_depth += 1
        if bold:
            self._bold_depth += 1
        if heading:
            self._heading_depth += 1
            self._block_heading = True

    def _end(self, name: str) -> None:
        if not self._skipping() and (
            name in _BLOCK_TAGS or name.split(":")[-1] in _BLOCK_TAGS or name in _HEADING_TAGS
        ):
            self._flush()
        self._pop(name)

    def _flush(self) -> None:
        if self._pieces:
            heading = self._block_heading or self._heading_depth > 0
            self._blocks.append((self._pieces, heading))
            self._pieces = []
        self._block_heading = False

    def _skipping(self) -> bool:
        return any(tag.skip for tag in self._open)

    def _pop(self, name: str) -> None:
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index].name != name:
                continue
            node = self._open.pop(index)
            if node.link:
                self._link_depth = max(0, self._link_depth - 1)
            if node.bold:
                self._bold_depth = max(0, self._bold_depth - 1)
            if node.heading:
                self._heading_depth = max(0, self._heading_depth - 1)
            return


def _should_skip(name: str, attrs: list[tuple[str, str | None]]) -> bool:
    local = name.split(":")[-1]
    prefix = name.split(":")[0] if ":" in name else ""
    if name in _SKIP_TAGS or local in _SKIP_TAGS:
        return True
    if prefix == "ix" and local in {"header", "hidden"}:
        return True
    style = _attribute(attrs, "style")
    return style is not None and _HIDDEN_STYLE_RE.search(style) is not None


def _has_href(attrs: list[tuple[str, str | None]]) -> bool:
    href = _attribute(attrs, "href")
    return href is not None and href.strip() != ""


def _render_block(pieces: list[_Piece], *, heading_tag: bool) -> _Draft | None:
    text = _normalize_line("".join(piece.text for piece in pieces))
    if text == "":
        return None
    bold = False
    seen_text = False
    toc_link = True
    for piece in pieces:
        if piece.text.strip() == "":
            continue
        if not seen_text:
            bold = piece.bold
            seen_text = True
        if not piece.in_link:
            toc_link = False
    if not seen_text:
        return None
    return _Draft(text=text, toc_link=toc_link, bold=bold or heading_tag, heading_tag=heading_tag)
