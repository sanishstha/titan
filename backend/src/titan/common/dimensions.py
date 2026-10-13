"""Canonical XBRL dimension identity.

``dimensions_hash`` is the SHA-256 of compact JSON with sorted keys.
An unsegmented total is ``{}``, whose hash is stable and not the same as a
missing object. Segment facts therefore cannot collapse onto the company total.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_dimensions_json(dimensions: Mapping[str, Any]) -> str:
    """Return the identity JSON for one fact's dimensions."""
    if not isinstance(dimensions, Mapping):
        raise TypeError("dimensions must be a JSON object")
    normalized: dict[str, str] = {}
    for key, value in dimensions.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("dimension keys and values must be strings")
        normalized[key] = value
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_dimensions(dimensions: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of :func:`canonical_dimensions_json`."""
    payload = canonical_dimensions_json(dimensions).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
