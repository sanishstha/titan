"""Command line. ``titan ingest`` downloads primary SEC filings for one ticker."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from titan.common.session import open_session
from titan.ingest.errors import SecIngestError, SecTransportError
from titan.ingest.run import (
    IncrementalIngestResult,
    IngestFailure,
    IngestProgress,
    ingest_incrementally,
)
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.store import StoredFiling
from titan.ingest.submissions import parse_since
from titan.ingest.tickers import resolve_ticker

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """TITAN command line."""


@app.command()
def ingest(
    ticker: Annotated[str, typer.Option("--ticker", help="SEC ticker symbol, such as NVDA.")],
    since: Annotated[
        str,
        typer.Option("--since", help="Year or YYYY-MM-DD. Filing dates on or after this are kept."),
    ],
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=1,
            help="Download at most this many filings. Omit it to ingest every match.",
        ),
    ] = None,
) -> None:
    """Download 10-K, 10-Q, and 8-K primary documents for one company."""
    try:
        since_date = parse_since(since)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    try:
        result = run_ingest(ticker, since_date, limit=limit, progress=_progress)
    except (SecTransportError, SecIngestError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(_report(result))
    for failure in result.failures:
        typer.echo(_failure_line(failure), err=True)
    if result.failures:
        raise typer.Exit(1)


def run_ingest(
    ticker: str,
    since: date,
    *,
    client: SecHttpClient | None = None,
    session: Session | None = None,
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> IncrementalIngestResult:
    """Resolve ``ticker`` and store its primary filings on or after ``since``.

    When ``session`` is omitted, each successful filing is committed before the
    next one starts. A passed session is left for the caller to commit or roll
    back. ``limit`` bounds how many primary documents are downloaded.
    """
    owns_client = client is None
    http = SecHttpClient() if client is None else client
    try:
        match = resolve_ticker(http, ticker)
        if session is not None:
            return ingest_incrementally(
                session,
                http,
                cik=match.cik,
                since=since,
                ticker=match.ticker,
                limit=limit,
                commit_each=False,
                progress=progress,
            )
        with open_session() as owned:
            try:
                return ingest_incrementally(
                    owned,
                    http,
                    cik=match.cik,
                    since=since,
                    ticker=match.ticker,
                    limit=limit,
                    commit_each=True,
                    progress=progress,
                )
            except Exception:
                owned.rollback()
                raise
    finally:
        if owns_client:
            http.close()


def _progress(event: StoredFiling | IngestFailure) -> None:
    if isinstance(event, IngestFailure):
        typer.echo(
            f"failed {event.accession_number} {event.stage} {event.error_type}",
            err=True,
        )
        return
    state = "inserted" if event.inserted else "unchanged"
    typer.echo(f"{state} {event.accession_number} {event.form_type}", err=True)


def _report(result: IncrementalIngestResult) -> str:
    counts = result.counts
    return "\n".join(
        (
            f"{result.discovery.cik} {result.discovery.company_name}",
            (
                f"discovered {counts.discovered} "
                f"inserted {counts.inserted} "
                f"unchanged {counts.unchanged} "
                f"failed {counts.failed}"
            ),
            f"documents {result.document_count}",
            f"row_sha256 {result.row_sha256}",
            f"content_sha256 {result.content_sha256}",
        )
    )


def _failure_line(failure: IngestFailure) -> str:
    return (
        f"failed {failure.accession_number} {failure.stage} "
        f"{failure.error_type}: {failure.message}"
    )
