"""Resolve an SEC ticker to a CIK using the company ticker list.

``https://www.sec.gov/files/company_tickers.json`` is the public map from
ticker to CIK. A ticker that is missing, or that points at two CIKs, is an
error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import cast

from titan.ingest.errors import SecIdentityError, SecSubmissionError
from titan.ingest.identifiers import normalize_cik
from titan.ingest.sec_transport import SecHttpClient, canonical_sec_url

_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,9}$")
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class TickerMatch:
    """One ticker and the CIK it names in the SEC list."""

    ticker: str
    cik: str


def company_tickers_url() -> str:
    """Return the SEC company ticker list URL."""
    return canonical_sec_url(_TICKERS_URL)


def normalize_ticker(value: str) -> str:
    """Return an uppercase ticker symbol."""
    if not isinstance(value, str):
        raise SecIdentityError("ticker must be a string")
    symbol = value.strip().upper()
    if _TICKER.fullmatch(symbol) is None:
        raise SecIdentityError("ticker must be a symbol such as NVDA")
    return symbol


def match_ticker(payload: bytes, ticker: str) -> TickerMatch:
    """Return the CIK for ``ticker`` in one company-ticker payload."""
    symbol = normalize_ticker(ticker)
    matches = _matches(payload, symbol)
    if not matches:
        raise SecIdentityError(f"ticker {symbol} is not in the SEC company ticker list")
    if len(matches) > 1:
        raise SecIdentityError(f"ticker {symbol} matches more than one CIK")
    cik = next(iter(matches))
    return TickerMatch(ticker=symbol, cik=cik)


def resolve_ticker(client: SecHttpClient, ticker: str) -> TickerMatch:
    """Fetch the SEC ticker list and return the CIK for ``ticker``."""
    body = client.get(company_tickers_url()).body
    return match_ticker(body, ticker)


def _matches(payload: bytes, symbol: str) -> set[str]:
    if not isinstance(payload, bytes) or payload == b"":
        raise SecSubmissionError("company ticker list is empty")
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SecSubmissionError("company ticker list is not JSON") from exc
    if not isinstance(loaded, dict):
        raise SecSubmissionError("company ticker list must be a JSON object")
    found: set[str] = set()
    for value in cast(dict[object, object], loaded).values():
        cik = _matching_cik(value, symbol)
        if cik is not None:
            found.add(cik)
    return found


def _matching_cik(value: object, symbol: str) -> str | None:
    if not isinstance(value, dict):
        raise SecSubmissionError("company ticker list entries must be JSON objects")
    entry = cast(dict[object, object], value)
    raw_ticker = entry.get("ticker")
    if not isinstance(raw_ticker, str) or raw_ticker.strip().upper() != symbol:
        return None
    return _cik(entry.get("cik_str"), symbol)


def _cik(value: object, symbol: str) -> str:
    if type(value) is not int:
        raise SecSubmissionError(f"ticker {symbol} has a CIK that is not an integer")
    return normalize_cik(str(value))
