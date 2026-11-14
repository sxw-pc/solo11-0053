"""Three-state decision synthesis with an explainable reason chain.

决策分三层组织，互不耦合：

1. :func:`_get` / :func:`_from_spec` 负责 dict 与 dataclass 的鸭子类型适配；
2. :func:`_bucket_by_status` 负责把断言结果按状态分桶并产出固定键序的计数；
3. :mod:`gatesampler.decision` 内部的规则注册表负责规则判定，:func:`decide`
   只做顺序分派，自身不含任何规则分支。新增规则只需用 :func:`_register_rule`
   注册一个策略对象，无需修改分派逻辑。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

# counts 字典的固定键集合与顺序，分桶实现不得增减或重排。
_COUNT_KEYS = ("pass", "fail", "warn", "skip")


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


@dataclass(frozen=True)
class _DecisionSettings:
    """从 spec / 关键字参数解析出的决策阈值，解析过程与规则判定分离。"""

    block_on_fail: bool
    review_on_warn_count: int
    review_on_skip_count: int | None
    block_on_fail_count: int | None


@dataclass(frozen=True)
class _StatusBuckets:
    """按状态分桶的断言结果，以及固定键序的四态计数。"""

    items: tuple[Any, ...]
    counts: dict[str, int]
    fails: tuple[Any, ...]
    warns: tuple[Any, ...]
    skips: tuple[Any, ...]


def _resolve_settings(
    spec: Any,
    *,
    block_on_fail: bool | None,
    review_on_warn_count: int | None,
    review_on_skip_count: int | None,
    block_on_fail_count: int | None,
) -> _DecisionSettings:
    return _DecisionSettings(
        block_on_fail=(
            _from_spec(spec, "block_on_fail", True) if block_on_fail is None else block_on_fail
        ),
        review_on_warn_count=(
            _from_spec(spec, "review_on_warn_count", 1)
            if review_on_warn_count is None
            else review_on_warn_count
        ),
        review_on_skip_count=(
            _from_spec(spec, "review_on_skip_count", None)
            if review_on_skip_count is None
            else review_on_skip_count
        ),
        block_on_fail_count=(
            _from_spec(spec, "block_on_fail_count", None)
            if block_on_fail_count is None
            else block_on_fail_count
        ),
    )


def _bucket_by_status(items: list[Any]) -> _StatusBuckets:
    counts = {key: 0 for key in _COUNT_KEYS}
    buckets: dict[str, list[Any]] = {key: [] for key in _COUNT_KEYS}
    for item in items:
        status = _get(item, "status")
        if status in buckets:
            buckets[status].append(item)
            counts[status] += 1
    return _StatusBuckets(
        items=tuple(items),
        counts=counts,
        fails=tuple(buckets["fail"]),
        warns=tuple(buckets["warn"]),
        skips=tuple(buckets["skip"]),
    )


@dataclass(frozen=True)
class _RuleContext:
    settings: _DecisionSettings
    buckets: _StatusBuckets


# 规则策略：predicate 命中即返回，build_reasons 产出与历史契约逐字节一致的理由链。
@dataclass(frozen=True)
class _DecisionRule:
    rule_code: str
    rule: str
    outcome: str
    predicate: Callable[[_RuleContext], bool]
    build_reasons: Callable[[_RuleContext], list[dict[str, Any]]]


_RULE_REGISTRY: list[_DecisionRule] = []


def _register_rule(
    rule_code: str,
    rule: str,
    outcome: str,
    predicate: Callable[[_RuleContext], bool],
    build_reasons: Callable[[_RuleContext], list[dict[str, Any]]],
) -> _DecisionRule:
    """注册一条决策规则；注册顺序即分派优先级，命中即停止。"""
    rule_obj = _DecisionRule(rule_code, rule, outcome, predicate, build_reasons)
    _RULE_REGISTRY.append(rule_obj)
    return rule_obj


def _reason_for(item: Any, rule_code: str, rule: str, outcome: str) -> dict[str, Any]:
    return _assertion_reason(item, rule_code, rule, outcome, _get(item, "message", ""))


# --- GS-DEC-001：任一 fail 且开启硬拦截，整批 block -------------------------
def _any_fail_blocks_predicate(ctx: _RuleContext) -> bool:
    return bool(ctx.buckets.fails) and ctx.settings.block_on_fail


def _any_fail_blocks_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    reasons = [
        _summary(
            "GS-DEC-001",
            "any_fail_blocks",
            "block",
            f"存在 {len(ctx.buckets.fails)} 条 fail 断言；block_on_fail=true，整批拦截",
        )
    ]
    reasons.extend(
        _reason_for(item, "GS-DEC-001", "any_fail_blocks", "block")
        for item in ctx.buckets.fails
    )
    return reasons


_register_rule(
    "GS-DEC-001", "any_fail_blocks", "block",
    _any_fail_blocks_predicate, _any_fail_blocks_reasons,
)


# --- GS-DEC-006：fail 数量达到 block_on_fail_count 阈值，整批 block --------
def _fail_count_blocks_predicate(ctx: _RuleContext) -> bool:
    threshold = ctx.settings.block_on_fail_count
    return threshold is not None and len(ctx.buckets.fails) >= int(threshold)


def _fail_count_blocks_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    threshold = ctx.settings.block_on_fail_count
    reasons = [
        _summary(
            "GS-DEC-006",
            "fail_count_blocks",
            "block",
            f"fail 数量 {len(ctx.buckets.fails)} ≥ 阈值 {threshold}，整批拦截",
        )
    ]
    reasons.extend(
        _reason_for(item, "GS-DEC-006", "fail_count_blocks", "block")
        for item in ctx.buckets.fails
    )
    return reasons


_register_rule(
    "GS-DEC-006", "fail_count_blocks", "block",
    _fail_count_blocks_predicate, _fail_count_blocks_reasons,
)


# --- GS-DEC-004：存在 fail 但未拦截，转人工（warn 理由并入同一条链） -------
def _fail_requires_review_predicate(ctx: _RuleContext) -> bool:
    return bool(ctx.buckets.fails)


def _fail_requires_review_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    reasons = [
        _summary(
            "GS-DEC-004",
            "fail_requires_review",
            "review",
            f"存在 {len(ctx.buckets.fails)} 条 fail 断言；block_on_fail=false，转人工复核",
        )
    ]
    reasons.extend(
        _reason_for(item, "GS-DEC-004", "fail_requires_review", "review")
        for item in ctx.buckets.fails
    )
    reasons.extend(
        _reason_for(item, "GS-DEC-002", "warn_review", "review")
        for item in ctx.buckets.warns
    )
    return reasons


_register_rule(
    "GS-DEC-004", "fail_requires_review", "review",
    _fail_requires_review_predicate, _fail_requires_review_reasons,
)


# --- GS-DEC-002：warn 数量达到阈值，转人工 --------------------------------
def _warn_review_predicate(ctx: _RuleContext) -> bool:
    return len(ctx.buckets.warns) >= int(ctx.settings.review_on_warn_count)


def _warn_review_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    limit = ctx.settings.review_on_warn_count
    reasons = [
        _summary(
            "GS-DEC-002",
            "warn_review",
            "review",
            f"warn 数量 {len(ctx.buckets.warns)} ≥ 阈值 {limit}，转人工复核",
        )
    ]
    reasons.extend(
        _reason_for(item, "GS-DEC-002", "warn_review", "review")
        for item in ctx.buckets.warns
    )
    return reasons


_register_rule(
    "GS-DEC-002", "warn_review", "review",
    _warn_review_predicate, _warn_review_reasons,
)


# --- GS-DEC-005：skip 数量达到阈值，转人工 --------------------------------
def _skip_review_predicate(ctx: _RuleContext) -> bool:
    limit = ctx.settings.review_on_skip_count
    return limit is not None and len(ctx.buckets.skips) >= int(limit)


def _skip_review_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    limit = ctx.settings.review_on_skip_count
    reasons = [
        _summary(
            "GS-DEC-005",
            "skip_review",
            "review",
            f"skip 数量 {len(ctx.buckets.skips)} ≥ 阈值 {limit}，转人工复核",
        )
    ]
    reasons.extend(
        _reason_for(item, "GS-DEC-005", "skip_review", "review")
        for item in ctx.buckets.skips
    )
    return reasons


_register_rule(
    "GS-DEC-005", "skip_review", "review",
    _skip_review_predicate, _skip_review_reasons,
)


# --- GS-DEC-003：兜底放行（含 results 为空的 all_clear 场景） --------------
def _all_clear_reasons(ctx: _RuleContext) -> list[dict[str, Any]]:
    limit = ctx.settings.review_on_warn_count
    return [
        _summary(
            "GS-DEC-003",
            "all_clear",
            "pass",
            f"没有 fail 触发拦截，warn 数量 {len(ctx.buckets.warns)} 未达阈值 {limit}，放行",
        )
    ]


_register_rule(
    "GS-DEC-003", "all_clear", "pass",
    lambda ctx: True, _all_clear_reasons,
)


def decide(
    results: Iterable[Any],
    spec: Any = None,
    *,
    block_on_fail: bool | None = None,
    review_on_warn_count: int | None = None,
    review_on_skip_count: int | None = None,
    block_on_fail_count: int | None = None,
) -> DecisionResult:
    """Combine guard results without touching files or the ledger."""
    buckets = _bucket_by_status(list(results))
    context = _RuleContext(
        _resolve_settings(
            spec,
            block_on_fail=block_on_fail,
            review_on_warn_count=review_on_warn_count,
            review_on_skip_count=review_on_skip_count,
            block_on_fail_count=block_on_fail_count,
        ),
        buckets,
    )
    for rule in _RULE_REGISTRY:
        if rule.predicate(context):
            return DecisionResult(rule.outcome, tuple(rule.build_reasons(context)), buckets.counts)
    raise RuntimeError("决策规则注册表缺少兜底规则，无法合成决策")  # pragma: no cover
