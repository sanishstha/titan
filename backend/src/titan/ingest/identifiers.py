"""SEC filing identities and the archive URL they are allowed to name.

CIKs are normalized to ten digits. Accession numbers stay in dashed form.
``primaryDocument`` may name a file inside the filing directory, including one
subdirectory, and it may not climb out of that directory. The archive URL is
built only after those checks and is checked again against the filing prefix.
"""

from __future__ import annotations

import re

import httpx

from titan.ingest.errors import SecIdentityError, SecUrlError
from titan.ingest.sec_transport import canonical_sec_url

_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_OLDER_FILE = re.compile(r"^CIK([0-9]{10})-submissions-([0-9]{3})\.json$")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_MAX_PRIMARY_LENGTH = 512


def normalize_cik(value: object) -> str:
    """Return a zero-padded 10-digit CIK."""
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= len(value) <= 10
    ):
        raise SecIdentityError("CIK must be a 1 to 10 digit string")
    if int(value) == 0:
        raise SecIdentityError("CIK must not be zero")
    return f"{int(value):010d}"


def validate_accession(value: object) -> str:
    """Return a dashed accession number.

    The first ten digits are the submitter's CIK. A company filing can carry
    a filing agent's accession, so those digits are not required to equal the
    registrant CIK.
    """
    if not isinstance(value, str) or _ACCESSION.fullmatch(value) is None:
        raise SecIdentityError("accession number must use the form ##########-##-######")
    return value


def validate_submission_filename(value: object, *, cik: str) -> str:
    """Return an older submissions filename for this CIK."""
    normalized = normalize_cik(cik)
    if not isinstance(value, str):
        raise SecIdentityError("submission file name must be a string")
    match = _OLDER_FILE.fullmatch(value)
    if match is None or match.group(1) != normalized:
        raise SecIdentityError(
            "submission file name is not a CIK submissions file for this company"
        )
    return value


def validate_primary_document(value: object) -> str:
    """Return a primaryDocument that stays inside its filing directory."""
    if not isinstance(value, str) or value == "":
        raise SecIdentityError("primaryDocument must be a relative filing filename")
    if len(value) > _MAX_PRIMARY_LENGTH:
        raise SecIdentityError("primaryDocument is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SecIdentityError("primaryDocument contains a control character")
    if any(character in value for character in "%\\?#") or value.startswith("/") or "//" in value:
        raise SecIdentityError("primaryDocument must not contain a path escape")
    for segment in value.split("/"):
        if segment in {"", ".", ".."} or _SEGMENT.fullmatch(segment) is None:
            raise SecIdentityError("primaryDocument must not contain a path escape")
    return value


def submissions_index_url(cik: str) -> str:
    """Return the company submissions URL for a CIK."""
    normalized = normalize_cik(cik)
    return canonical_sec_url(f"https://data.sec.gov/submissions/CIK{normalized}.json")


def older_submissions_url(filename: str, *, cik: str) -> str:
    """Return the URL for one referenced older submissions file."""
    name = validate_submission_filename(filename, cik=cik)
    return canonical_sec_url(f"https://data.sec.gov/submissions/{name}")


def archive_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    """Return the immutable EDGAR archive URL for one primary document.

    The directory CIK is the registrant, without leading zeros. The accession
    in the path has no dashes. A primaryDocument that escapes
    ``/Archives/edgar/data/{cik}/{accession}/`` is rejected.
    """
    normalized = normalize_cik(cik)
    accession = validate_accession(accession_number)
    document = validate_primary_document(primary_document)
    unpadded = str(int(normalized))
    compact = accession.replace("-", "")
    prefix = f"/Archives/edgar/data/{unpadded}/{compact}/"
    try:
        url = canonical_sec_url(f"https://www.sec.gov{prefix}{document}")
    except SecUrlError as exc:
        raise SecIdentityError("primaryDocument escaped the filing directory") from exc
    parsed = httpx.URL(url)
    if (
        parsed.host != "www.sec.gov"
        or parsed.query
        or not parsed.path.startswith(prefix)
        or parsed.path[len(prefix) :] != document
    ):
        raise SecIdentityError("primaryDocument escaped the filing directory")
    return url
