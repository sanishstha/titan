"""Errors raised by the SEC transport and the raw response cache."""


class SecTransportError(Exception):
    """Base error for SEC requests and the on-disk raw cache."""


class SecConfigError(SecTransportError):
    """Settings break a SEC fair-access limit or omit a descriptive User-Agent."""


class SecUrlError(SecTransportError):
    """The URL is not an HTTPS request to an allowed SEC host."""


class SecForbiddenError(SecTransportError):
    """SEC returned 403. The request is not retried."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"SEC returned 403 for {url}. The request was not retried.")


class SecHttpStatusError(SecTransportError):
    """SEC returned a status that is not retried."""

    def __init__(self, url: str, status_code: int) -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(f"SEC returned HTTP {status_code} for {url}")


class SecRedirectError(SecTransportError):
    """SEC returned a redirect. Redirects are not followed."""

    def __init__(self, url: str, status_code: int) -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(
            f"SEC returned redirect {status_code} for {url}. Redirects are not followed."
        )


class SecResponseTooLarge(SecTransportError):
    """A response is larger than the configured byte limit."""

    def __init__(self, url: str, size: int, limit: int) -> None:
        self.url = url
        self.size = size
        self.limit = limit
        super().__init__(f"SEC response for {url} is {size} bytes, over the limit of {limit}")


class SecRetryError(SecTransportError):
    """A retryable SEC request still failed after the bounded number of attempts."""

    def __init__(
        self,
        url: str,
        *,
        attempts: int,
        status_code: int | None,
    ) -> None:
        self.url = url
        self.attempts = attempts
        self.status_code = status_code
        if status_code is None:
            detail = "transient network error"
        else:
            detail = f"HTTP {status_code}"
        super().__init__(f"SEC request failed after {attempts} attempts for {url} ({detail})")


class SecRetryAfterExceeded(SecTransportError):
    """SEC asked us to wait longer than the configured ceiling.

    The client does not retry sooner than that ``Retry-After`` value.
    """

    def __init__(self, url: str, *, retry_after_seconds: float, ceiling: float) -> None:
        self.url = url
        self.retry_after_seconds = retry_after_seconds
        self.ceiling = ceiling
        super().__init__(
            f"SEC asked to wait {retry_after_seconds}s for {url}, above the {ceiling}s ceiling. "
            "The request was not retried early."
        )


class CacheIntegrityError(SecTransportError):
    """A cache file is truncated, mislabeled, or does not match its SHA-256."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        self.url = url
        if url is None:
            super().__init__(message)
        else:
            super().__init__(f"{message} ({url})")


class SecIngestError(SecTransportError):
    """A submissions payload or filing identity cannot be ingested."""


class SecIdentityError(SecIngestError):
    """CIK, accession, submission filename, or primaryDocument failed validation."""


class SecSubmissionError(SecIngestError):
    """The submissions JSON is not a usable filing index."""


class SecAcceptanceError(SecIngestError):
    """acceptanceDateTime cannot be converted without guessing."""


class FilingConflictError(SecIngestError):
    """One accession was seen twice with different metadata, or the stored row disagrees."""

    def __init__(self, accession_number: str, message: str) -> None:
        self.accession_number = accession_number
        super().__init__(f"{accession_number}: {message}")


class FilingSectionError(SecIngestError):
    """A filing body or form type cannot be parsed into sections."""
