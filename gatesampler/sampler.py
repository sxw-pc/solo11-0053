"""Deterministic systematic, stratified, and reservoir sampling."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import GateSamplerError
from .util import canonical_json, sha256_text, stable_scalar


@dataclass(frozen=True)
class SampleResult:
    method: str
    seed: int
    size_requested: int
    size_actual: int
    indices: tuple[int, ...]
    details: dict[str, Any]

    @property
    def sample_fingerprint(self) -> str:
        payload = {
            "method": self.method,
            "seed": self.seed,
            "indices": list(self.indices),
            "size": self.size_actual,
        }
        return sha256_text(canonical_json(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "seed": self.seed,
            "size_requested": self.size_requested,
            "size_actual": self.size_actual,
            "indices": list(self.indices),
            "sample_fingerprint": self.sample_fingerprint,
            "details": self.details,
        }


def _field(sampling: Any, key: str, default: Any = None) -> Any:
    if isinstance(sampling, dict):
        return sampling.get(key, default)
    return getattr(sampling, key, default)


def target_size(total: int, sampling: Any) -> int:
    size = _field(sampling, "size")
    fraction = _field(sampling, "fraction")
    if size is not None:
        requested = size
    elif fraction is not None:
        requested = math.ceil(total * float(fraction))
    else:
        raise GateSamplerError("GS-SMP-001", "抽样配置缺少 size 或 fraction")
    return min(total, requested)


def systematic_indices(total: int, size: int, seed: int) -> list[int]:
    """Equidistant sampling with a seeded random starting offset."""
    if total <= 0 or size <= 0:
        return []
    count = min(total, size)
    if count == total:
        return list(range(total))
    interval = total / count
    rng = random.Random(seed)
    start = rng.random() * interval
    return [min(total - 1, int(start + index * interval)) for index in range(count)]


def reservoir_indices(total: int, size: int, seed: int) -> list[int]:
    """Single-pass Algorithm R over an index stream; output is index-sorted."""
    if total <= 0 or size <= 0:
        return []
    count = min(total, size)
    rng = random.Random(seed)
    reservoir = list(range(count))
    for index in range(count, total):
        candidate = rng.randrange(index + 1)
        if candidate < count:
            reservoir[candidate] = index
    return sorted(reservoir)


def _derive_seed(seed: int, stratum: str) -> int:
    digest = hashlib.sha256(f"{seed}:{stratum}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _allocate(target: int, sizes: list[int]) -> list[int]:
    """Allocate an exact target across strata by largest-remainder preference."""
    count = len(sizes)
    if target < count:
        raise GateSamplerError(
            "GS-SMP-001", f"分层抽样目标 {target} 小于非空层数 {count}，无法覆盖每层"
        )
    total = sum(sizes)
    quotas = [target * size / total for size in sizes]
    allocation = [min(size, max(1, int(quota))) for size, quota in zip(sizes, quotas)]
    while sum(allocation) < target:
        candidates = [i for i, size in enumerate(sizes) if allocation[i] < size]
        if not candidates:
            break
        chosen = max(candidates, key=lambda i: (quotas[i] - allocation[i], -i))
        allocation[chosen] += 1
    while sum(allocation) > target:
        candidates = [i for i, value in enumerate(allocation) if value > 1]
        if not candidates:
            break
        chosen = max(candidates, key=lambda i: (allocation[i] - quotas[i], -i))
        allocation[chosen] -= 1
    return allocation


def _strata_fields(strata_field: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize a single field or field list; reject empty declarations."""
    if isinstance(strata_field, str):
        fields = (strata_field,) if strata_field else ()
    else:
        fields = tuple(strata_field or ())
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise GateSamplerError("GS-SMP-001", "分层抽样缺少有效的 strata_field")
    return fields


def _strata_key(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    """Build a stable stratum identity from one or more fields.

    Each component uses the type-aware ``stable_scalar`` encoding, so ``None``,
    strings, booleans, and numbers never collide.  Multi-field keys are a
    canonical JSON array, and field order comes from the contract, so neither
    dict/row iteration order nor null values can change a stratum's identity.
    """
    if len(fields) == 1:
        return stable_scalar(row.get(fields[0]))
    return canonical_json([stable_scalar(row.get(field)) for field in fields])


def _allocate_with_minimum(
    target: int, sizes: list[int], per_stratum_min: int
) -> tuple[list[int], list[int], bool]:
    """Cover every stratum up to ``min(size, per_stratum_min)``, then fill.

    When the requested minimums exceed ``target`` the allocation is reduced
    globally instead of truncated: one slot at a time is taken from the stratum
    with the largest current allocation (ties: larger population, then earlier
    stratum key), never shrinking any stratum below one row.  Strata left below
    their requested minimum are reported by the caller as undercovered.
    """
    count = len(sizes)
    if target < count:
        raise GateSamplerError(
            "GS-SMP-001",
            f"分层抽样目标 {target} 小于非空层数 {count}，无法满足每层最小覆盖",
        )
    minimums = [min(size, per_stratum_min) for size in sizes]
    allocation = list(minimums)
    total = sum(sizes)
    quotas = [target * size / total for size in sizes]
    budget_limited = sum(allocation) > target
    if not budget_limited:
        while sum(allocation) < target:
            candidates = [i for i, size in enumerate(sizes) if allocation[i] < size]
            if not candidates:
                break
            chosen = max(candidates, key=lambda i: (quotas[i] - allocation[i], -i))
            allocation[chosen] += 1
        return allocation, minimums, False
    excess = sum(allocation) - target
    while excess > 0:
        candidates = [i for i in range(count) if allocation[i] > 1]
        if not candidates:  # pragma: no cover - target >= count guarantees this
            raise GateSamplerError(
                "GS-SMP-001",
                f"分层抽样目标 {target} 不足以满足每层最小覆盖 1",
            )
        chosen = max(candidates, key=lambda i: (allocation[i], sizes[i], -i))
        allocation[chosen] -= 1
        excess -= 1
    return allocation, minimums, True


def stratified_indices(
    rows: Sequence[dict[str, Any]],
    size: int,
    seed: int,
    strata_field: str | Sequence[str],
    *,
    per_stratum_min: int | None = None,
) -> tuple[list[int], dict[str, Any]]:
    fields = _strata_fields(strata_field)
    if per_stratum_min is not None and (
        isinstance(per_stratum_min, bool)
        or not isinstance(per_stratum_min, int)
        or per_stratum_min < 1
    ):
        raise GateSamplerError("GS-SMP-001", "per_stratum_min 必须是正整数")
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(_strata_key(row, fields), []).append(index)
    if not groups:
        details: dict[str, Any] = {"strata": []}
        if per_stratum_min is not None:
            details.update(
                {
                    "per_stratum_min": per_stratum_min,
                    "undercoverage": False,
                    "undercoverage_strata": [],
                    "minimum_shortfall": 0,
                    "budget_limited": False,
                }
            )
        return [], details
    keys = sorted(groups)
    target = min(len(rows), size)
    sizes = [len(groups[key]) for key in keys]
    if per_stratum_min is None:
        allocation = _allocate(target, sizes)
        minimums: list[int] | None = None
        budget_limited = False
    else:
        allocation, minimums, budget_limited = _allocate_with_minimum(
            target, sizes, per_stratum_min
        )
    selected: list[int] = []
    strata_details: list[dict[str, Any]] = []
    for index, (key, amount) in enumerate(zip(keys, allocation)):
        group = groups[key]
        chosen = systematic_indices(len(group), amount, _derive_seed(seed, key))
        selected.extend(group[index] for index in chosen)
        detail: dict[str, Any] = {
            "stratum": key,
            "population": len(group),
            "sample": amount,
            "sample_indices": [group[index] for index in chosen],
        }
        if minimums is not None:
            detail["minimum"] = minimums[index]
            detail["undercoverage"] = amount < minimums[index]
        strata_details.append(detail)
    details = {"strata": strata_details}
    if minimums is not None:
        undercovered = [
            detail["stratum"] for detail in strata_details if detail["undercoverage"]
        ]
        details.update(
            {
                "per_stratum_min": per_stratum_min,
                "undercoverage": bool(undercovered),
                "undercoverage_strata": undercovered,
                "minimum_shortfall": sum(
                    max(0, minimums[index] - allocation[index])
                    for index in range(len(allocation))
                ),
                "budget_limited": budget_limited,
            }
        )
    return sorted(selected), details


def sample_rows(rows: Sequence[Any], sampling: Any) -> SampleResult:
    """Pure sampling entry point: same rows + config => identical result."""
    total = len(rows)
    method = str(_field(sampling, "method"))
    seed = int(_field(sampling, "seed"))
    requested = target_size(total, sampling)
    details: dict[str, Any] = {}
    if method == "systematic":
        indices = systematic_indices(total, requested, seed)
        details = {"algorithm": "equidistant_random_start"}
    elif method == "reservoir":
        indices = reservoir_indices(total, requested, seed)
        details = {"algorithm": "algorithm_r"}
    elif method == "stratified":
        strata_raw = _field(sampling, "strata_field")
        if not strata_raw:
            raise GateSamplerError("GS-SMP-001", "分层抽样缺少 strata_field")
        fields = _strata_fields(strata_raw)
        declared: str | list[str] = (
            strata_raw if isinstance(strata_raw, str) else list(fields)
        )
        per_stratum_min = _field(sampling, "per_stratum_min")
        indices, details = stratified_indices(
            rows,
            requested,
            seed,
            declared,
            per_stratum_min=per_stratum_min,
        )
        details["algorithm"] = "stratified_systematic"
        details["strata_field"] = declared
    else:
        raise GateSamplerError("GS-SMP-001", f"未知抽样方法: {method}")
    return SampleResult(
        method=method,
        seed=seed,
        size_requested=requested,
        size_actual=len(indices),
        indices=tuple(indices),
        details=details,
    )
