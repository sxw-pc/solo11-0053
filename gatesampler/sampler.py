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


def stratified_indices(
    rows: Sequence[dict[str, Any]], size: int, seed: int, strata_field: str
) -> tuple[list[int], dict[str, Any]]:
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(stable_scalar(row.get(strata_field)), []).append(index)
    if not groups:
        return [], {"strata": []}
    keys = sorted(groups)
    target = min(len(rows), size)
    sizes = [len(groups[key]) for key in keys]
    allocation = _allocate(target, sizes)
    selected: list[int] = []
    strata_details: list[dict[str, Any]] = []
    for key, group, amount in zip(keys, (groups[key] for key in keys), allocation):
        chosen = systematic_indices(len(group), amount, _derive_seed(seed, key))
        selected.extend(group[index] for index in chosen)
        strata_details.append(
            {
                "stratum": key,
                "population": len(group),
                "sample": amount,
                "sample_indices": [group[index] for index in chosen],
            }
        )
    return sorted(selected), {"strata": strata_details}


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
        strata_field = _field(sampling, "strata_field")
        if not strata_field:
            raise GateSamplerError("GS-SMP-001", "分层抽样缺少 strata_field")
        indices, details = stratified_indices(rows, requested, seed, str(strata_field))
        details["algorithm"] = "stratified_systematic"
        details["strata_field"] = strata_field
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
