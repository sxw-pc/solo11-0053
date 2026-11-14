"""Three-state decision synthesis with an explainable reason chain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecisionResult:
    outcome: str
    reasons: tuple[dict[str, Any], ...]
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "reasons": list(self.reasons), "counts": dict(self.counts)}


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _from_spec(spec: Any, name: str, default: Any) -> Any:
    if spec is None:
        return default
    if isinstance(spec, dict):
        return spec.get(name, default)
    return getattr(spec, name, default)


def _assertion_reason(
    result: Any, rule_code: str, rule: str, outcome: str, message: str
) -> dict[str, Any]:
    return {
        "rule_code": rule_code,
        "rule": rule,
        "outcome": outcome,
        "assertion_id": _get(result, "assertion_id"),
        "assertion_status": _get(result, "status"),
        "assertion_code": _get(result, "code"),
        "message": message,
    }


def _summary(rule_code: str, rule: str, outcome: str, message: str) -> dict[str, Any]:
    return {"rule_code": rule_code, "rule": rule, "outcome": outcome, "message": message}


def decide(
    results: Iterable[Any],
    spec: Any = None,
    *,
    block_on_fail: bool | None = None,
    review_on_warn_count: int | None = None,
    review_on_skip_count: int | None = None,
) -> DecisionResult:
    """Combine guard results without touching files or the ledger."""
    items = list(results)
    statuses = [_get(item, "status") for item in items]
    counts = {
        "pass": statuses.count("pass"),
        "fail": statuses.count("fail"),
        "warn": statuses.count("warn"),
        "skip": statuses.count("skip"),
    }
    block = _from_spec(spec, "block_on_fail", True) if block_on_fail is None else block_on_fail
    warn_limit = (
        _from_spec(spec, "review_on_warn_count", 1)
        if review_on_warn_count is None
        else review_on_warn_count
    )
    skip_limit = (
        _from_spec(spec, "review_on_skip_count", None)
        if review_on_skip_count is None
        else review_on_skip_count
    )
    fails = [item for item in items if _get(item, "status") == "fail"]
    warns = [item for item in items if _get(item, "status") == "warn"]
    skips = [item for item in items if _get(item, "status") == "skip"]

    reasons: list[dict[str, Any]] = []
    if fails and block:
        reasons.append(
            _summary(
                "GS-DEC-001",
                "any_fail_blocks",
                "block",
                f"存在 {len(fails)} 条 fail 断言；block_on_fail=true，整批拦截",
            )
        )
        reasons.extend(
            _assertion_reason(item, "GS-DEC-001", "any_fail_blocks", "block", _get(item, "message", ""))
            for item in fails
        )
        outcome = "block"
    elif fails:
        reasons.append(
            _summary(
                "GS-DEC-004",
                "fail_requires_review",
                "review",
                f"存在 {len(fails)} 条 fail 断言；block_on_fail=false，转人工复核",
            )
        )
        reasons.extend(
            _assertion_reason(item, "GS-DEC-004", "fail_requires_review", "review", _get(item, "message", ""))
            for item in fails
        )
        reasons.extend(
            _assertion_reason(item, "GS-DEC-002", "warn_review", "review", _get(item, "message", ""))
            for item in warns
        )
        outcome = "review"
    elif len(warns) >= int(warn_limit):
        reasons.append(
            _summary(
                "GS-DEC-002",
                "warn_review",
                "review",
                f"warn 数量 {len(warns)} ≥ 阈值 {warn_limit}，转人工复核",
            )
        )
        reasons.extend(
            _assertion_reason(item, "GS-DEC-002", "warn_review", "review", _get(item, "message", ""))
            for item in warns
        )
        outcome = "review"
    elif skip_limit is not None and len(skips) >= int(skip_limit):
        reasons.append(
            _summary(
                "GS-DEC-005",
                "skip_review",
                "review",
                f"skip 数量 {len(skips)} ≥ 阈值 {skip_limit}，转人工复核",
            )
        )
        reasons.extend(
            _assertion_reason(item, "GS-DEC-005", "skip_review", "review", _get(item, "message", ""))
            for item in skips
        )
        outcome = "review"
    else:
        reasons.append(
            _summary(
                "GS-DEC-003",
                "all_clear",
                "pass",
                f"没有 fail 触发拦截，warn 数量 {len(warns)} 未达阈值 {warn_limit}，放行",
            )
        )
        outcome = "pass"
    return DecisionResult(outcome, tuple(reasons), counts)
