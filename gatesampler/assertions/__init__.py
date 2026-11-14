"""Pluggable assertion registry."""

from __future__ import annotations

from collections.abc import Sequence

from .base import Guard, GuardContext, GuardResult, RowRef
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


def build_guards(specs: Sequence) -> list[Guard]:
    guards = []
    for spec in specs:
        guard_class = GUARD_CLASSES.get(spec.type)
        if guard_class is None:
            raise ValueError(f"未注册的断言类型: {spec.type}")
        guards.append(guard_class(spec))
    return guards


def run_guards(specs: Sequence, context: GuardContext) -> list[GuardResult]:
    """Execute assertions in declaration order."""
    return [guard.evaluate(context) for guard in build_guards(specs)]


__all__ = [
    "GUARD_CLASSES",
    "Guard",
    "GuardContext",
    "GuardResult",
    "RowRef",
    "build_guards",
    "run_guards",
]
