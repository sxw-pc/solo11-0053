"""Small deterministic helpers shared across the package."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any, *, pretty: bool = False) -> str:
    """Serialize JSON deterministically (sorted keys, no NaN, fixed separators)."""
    if pretty:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ) + "\n"
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_scalar(value: Any) -> str:
    """Return a deterministic, type-aware string key for sorting/grouping."""
    if value is None:
        return "z:null"
    if isinstance(value, bool):
        return f"b:{int(value)}"
    if isinstance(value, (int, float)):
        return f"n:{value!r}"
    return f"s:{value}"


def make_run_id(batch_id: str, source_sha256: str, fingerprint: str) -> str:
    material = f"{batch_id}\x00{source_sha256}\x00{fingerprint}".encode()
    return "run-" + hashlib.sha256(material).hexdigest()[:24]
