"""Shared guard contracts, config-time validation rules and result helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..errors import GateSamplerError


def fail(code: str, message: str, location: str) -> None:
    raise GateSamplerError(code, message, location)


def text(value: Any, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        fail("GS-CFG-003", "缺少或非法字符串字段", location)
    return value.strip()


def integer(value: Any, location: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail("GS-CFG-004", "期望整数", location)
    if minimum is not None and value < minimum:
        fail("GS-CFG-004", f"整数不得小于 {minimum}", location)
    return value


def number(
    value: Any, location: str, *, low: float | None = None, high: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail("GS-CFG-004", "期望数值", location)
    result = float(value)
    if low is not None and result < low:
        fail("GS-CFG-004", f"数值不得小于 {low}", location)
    if high is not None and result > high:
        fail("GS-CFG-004", f"数值不得大于 {high}", location)
    return result


def boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        fail("GS-CFG-004", "期望布尔值", location)
    return value


def strings(value: Any, location: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        fail("GS-CFG-004", "期望非空字符串列表" if nonempty else "期望字符串列表", location)
    return [text(item, f"{location}[{index}]") for index, item in enumerate(value)]


def check_keys(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        fail("GS-CFG-030", f"未知断言参数: {', '.join(unknown)}", location)


@dataclass(frozen=True)
class ParamSpec:
    """Declarative config-time rule for a single assertion parameter.

    ``kind`` selects the shared validator; ``required`` means the validator is
    always invoked (even when the key is absent), matching the historical
    contract where e.g. a missing ``max_rate`` reports ``GS-CFG-004`` at
    ``...max_rate`` rather than a generic "missing field" error.
    """

    name: str
    kind: str
    required: bool = False
    minimum: int | float | None = None
    maximum: float | None = None
    value: Any = None
    message: str | None = None

    def validate(self, params: dict[str, Any], location: str, schema: Any) -> None:
        if self.required:
            raw = params.get(self.name)
        elif self.name in params:
            raw = params[self.name]
        else:
            return
        field_location = f"{location}.{self.name}"
        if self.kind == "integer":
            integer(raw, field_location, minimum=self.minimum)
        elif self.kind == "number":
            number(raw, field_location, low=self.minimum, high=self.maximum)
        elif self.kind == "boolean":
            boolean(raw, field_location)
        elif self.kind == "string":
            text(raw, field_location)
        elif self.kind == "column":
            value = text(raw, field_location)
            if value not in schema.columns:
                fail("GS-CFG-030", f"字段未在 schema 中声明: {value}", field_location)
        elif self.kind == "columns":
            for item in strings(raw, field_location, nonempty=True):
                if item not in schema.columns:
                    fail("GS-CFG-030", f"字段未在 schema 中声明: {item}", field_location)
        elif self.kind == "scalar_list":
            if (
                not isinstance(raw, list)
                or not raw
                or any(isinstance(item, (dict, list)) for item in raw)
            ):
                fail(
                    "GS-CFG-030",
                    self.message or f"{self.name} 必须是非空标量列表",
                    field_location,
                )
        elif self.kind == "constant":
            if raw != self.value:
                fail(
                    "GS-CFG-030",
                    self.message or f"{self.name} 只支持 {self.value}",
                    field_location,
                )
        else:  # pragma: no cover - developer error
            raise ValueError(f"未知参数校验类型: {self.kind}")


@dataclass(frozen=True)
class ParamRule:
    """A cross-field rule evaluated at the assertion's own location."""

    message: str
    predicate: Callable[[dict[str, Any]], bool]

    def validate(self, params: dict[str, Any], location: str, schema: Any) -> None:
        if not self.predicate(params):
            fail("GS-CFG-030", self.message, location)


ParamEntry = ParamSpec | ParamRule


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

    #: Declarative config-time parameter contract; consumed by config_validation.
    param_schema: tuple[ParamEntry, ...] = ()

    def __init__(self, spec: Any):
        self.spec = spec
        self.severity = getattr(spec, "severity", "fail")

    @classmethod
    def allowed_params(cls) -> set[str]:
        return {entry.name for entry in cls.param_schema if isinstance(entry, ParamSpec)}

    @classmethod
    def validate_params(cls, params: dict[str, Any], location: str, schema: Any) -> None:
        """Validate params in declaration order, preserving stable error contracts."""
        check_keys(params, cls.allowed_params(), location)
        for entry in cls.param_schema:
            entry.validate(params, location, schema)

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
