"""Pluggable assertion registry.

``GUARD_CLASSES`` is intentionally an insertion-ordered ``dict``: guard
execution order follows declaration order, and that order feeds the report
JSON byte hash used by replay. A single registry is the source of truth for
both execution and config-time validation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .base import (
    Guard,
    GuardContext,
    GuardResult,
    ParamEntry,
    ParamRule,
    ParamSpec,
    RowRef,
)
from .count_guard import CountGuard
from .domain_guard import DomainGuard
from .drift_guard import DriftGuard
from .duplicate_guard import DuplicateGuard
from .nullrate_guard import NullRateGuard
from .range_guard import RangeGuard
from .unique_guard import UniqueGuard

GUARD_CLASSES: dict[str, type[Guard]] = {
    guard.type_name: guard
    for guard in (
        CountGuard,
        UniqueGuard,
        DomainGuard,
        RangeGuard,
        NullRateGuard,
        DuplicateGuard,
        DriftGuard,
    )
}

#: Ordered guard type names, derived from the registry (never a set, so
#: declaration order stays observable).
GUARD_TYPE_NAMES: tuple[str, ...] = tuple(GUARD_CLASSES)
GUARD_TYPES: frozenset[str] = frozenset(GUARD_CLASSES)


def build_guards(specs: Sequence) -> list[Guard]:
    guards = []
    for spec in specs:
        guard_class = GUARD_CLASSES.get(spec.type)
        if guard_class is None:
            raise ValueError(f"未注册的断言类型: {spec.type}")
        guards.append(guard_class(spec))
    return guards


def validate_assertion_params(
    kind: str, params: dict[str, Any], location: str, schema: Any
) -> None:
    """Validate assertion params using the registered guard's declaration."""
    guard_class = GUARD_CLASSES.get(kind)
    if guard_class is None:
        raise ValueError(f"未注册的断言类型: {kind}")
    guard_class.validate_params(params, location, schema)


def run_guards(specs: Sequence, context: GuardContext) -> list[GuardResult]:
    """Execute assertions in declaration order."""
    return [guard.evaluate(context) for guard in build_guards(specs)]


__all__ = [
    "GUARD_CLASSES",
    "GUARD_TYPES",
    "GUARD_TYPE_NAMES",
    "Guard",
    "GuardContext",
    "GuardResult",
    "ParamEntry",
    "ParamRule",
    "ParamSpec",
    "RowRef",
    "build_guards",
    "run_guards",
    "validate_assertion_params",
]
