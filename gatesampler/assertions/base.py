"""Shared guard contracts and result helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RowRef:
    record_no: int
    data: Mapping[str, Any]


@dataclass
class GuardContext:
    sample: Sequence[RowRef]
    full: Sequence[RowRef]
    baselines: dict[str, Sequence[RowRef]] = field(default_factory=dict)
    batch: dict[str, Any] = field(default_factory=dict)
    column_types: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardResult:
    assertion_id: str
    type: str
    status: str
    code: str
    metric: Any
    threshold: Any
    evidence: dict[str, Any]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "type": self.type,
            "status": self.status,
            "code": self.code,
            "metric": self.metric,
            "threshold": self.threshold,
            "evidence": self.evidence,
            "message": self.message,
        }


class Guard:
    """Base class for pluggable assertions. Guards never perform file I/O."""

    type_name = "base"
    code = "GS-GATE-000"

    def __init__(self, spec: Any):
        self.spec = spec
        self.severity = getattr(spec, "severity", "fail")

    def evaluate(self, context: GuardContext) -> GuardResult:  # pragma: no cover - interface
        raise NotImplementedError

    def _result(
        self,
        *,
        violated: bool,
        metric: Any,
        threshold: Any,
        evidence: dict[str, Any],
        message: str,
    ) -> GuardResult:
        return GuardResult(
            assertion_id=self.spec.assertion_id,
            type=self.spec.type,
            status=self.severity if violated else "pass",
            code=self.code,
            metric=metric,
            threshold=threshold,
            evidence=evidence,
            message=message,
        )

    def _skip(self, message: str, evidence: dict[str, Any]) -> GuardResult:
        return GuardResult(
            assertion_id=self.spec.assertion_id,
            type=self.spec.type,
            status="skip",
            code=self.code,
            metric=None,
            threshold=None,
            evidence=evidence,
            message=message,
        )

    def _missing(self, context: GuardContext, columns: Sequence[str]) -> list[str]:
        return [name for name in columns if name not in context.column_types]

    @staticmethod
    def _rows(hits: Sequence[int], limit: int = 20) -> dict[str, Any]:
        unique = list(dict.fromkeys(int(item) for item in hits))
        return {"rows": unique[:limit], "truncated": len(unique) > limit}


def round_metric(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def key_label(values: Sequence[Any]) -> list[Any]:
    """JSON-safe composite key representation used in evidence."""
    return [None if value is None else value for value in values]
