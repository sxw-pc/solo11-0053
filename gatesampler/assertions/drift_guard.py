"""First-order distribution drift guard (max absolute share delta)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from .base import Guard, GuardContext, GuardResult, ParamSpec, round_metric


def _category(value: Any) -> str:
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _numeric(value: Any) -> bool:
    return value is not None and not isinstance(value, bool) and isinstance(value, (int, float))


def _categorical(values: Sequence[Any]) -> Counter:
    return Counter(_category(value) for value in values)


def _binned(values: Sequence[Any], low: float, high: float, bins: int) -> Counter:
    usable = [float(value) for value in values if _numeric(value)]
    if high == low:
        return Counter(_category(value) for value in usable)
    width = (high - low) / bins
    counts: Counter = Counter()
    for value in usable:
        index = min(bins - 1, max(0, int((value - low) / width)))
        counts[f"bin_{index}"] += 1
    return counts


class DriftGuard(Guard):
    type_name = "drift"
    code = "GS-GATE-107"
    param_schema = (
        ParamSpec("column", "column", required=True),
        ParamSpec("baseline", "string", required=True),
        ParamSpec("threshold", "number", required=True, minimum=0.0, maximum=1.0),
        ParamSpec("bins", "integer", minimum=2),
        ParamSpec(
            "metric",
            "constant",
            value="max_share_delta",
            message="目前只支持 max_share_delta",
        ),
    )

    def evaluate(self, context: GuardContext) -> GuardResult:
        column = self.spec.get("column")
        baseline_ref = self.spec.get("baseline")
        missing = self._missing(context, [column])
        if missing:
            return self._skip(
                f"漂移字段不存在: {column}",
                {"scope": "sample+baseline", "missing_columns": missing},
            )
        baseline = context.baselines.get(baseline_ref)
        if not baseline:
            return self._skip(
                "基线缺失或为空，无法计算分布漂移",
                {"scope": "sample+baseline", "baseline": baseline_ref, "reason": "baseline_missing"},
            )
        if not context.sample:
            return self._skip(
                "样本为空，无法计算分布漂移",
                {"scope": "sample+baseline", "baseline": baseline_ref, "sample_size": 0},
            )
        numeric = context.column_types.get(column) in {"int", "float"}
        bins = int(self.spec.get("bins", 5))
        baseline_values = [row.data.get(column) for row in baseline]
        sample_values = [row.data.get(column) for row in context.sample]
        meta: dict[str, Any] = {"numeric": numeric, "bins": bins if numeric else None}
        if numeric:
            usable = [float(value) for value in baseline_values if _numeric(value)]
            if not usable:
                return self._skip(
                    "基线没有可用数值，无法计算数值分箱漂移",
                    {"scope": "sample+baseline", "baseline": baseline_ref, "numeric": True},
                )
            low, high = min(usable), max(usable)
            baseline_counts = _binned(baseline_values, low, high, bins)
            sample_counts = _binned(sample_values, low, high, bins)
            meta.update({"baseline_min": low, "baseline_max": high, "nulls_count_as_values": False})
            if high > low:
                width = (high - low) / bins
                meta["bin_edges"] = [low + width * index for index in range(bins + 1)]
        else:
            baseline_counts = _categorical(baseline_values)
            sample_counts = _categorical(sample_values)
            meta["nulls_count_as_values"] = True
        baseline_total = sum(baseline_counts.values())
        sample_total = sum(sample_counts.values())
        if not baseline_total or not sample_total:
            return self._skip(
                "基线或样本没有可用值，无法计算分布漂移",
                {
                    "scope": "sample+baseline",
                    "baseline": baseline_ref,
                    "sample_usable": sample_total,
                    "baseline_usable": baseline_total,
                },
            )
        categories = sorted(set(baseline_counts) | set(sample_counts))
        deltas = []
        for category in categories:
            base_share = baseline_counts[category] / baseline_total
            sample_share = sample_counts[category] / sample_total
            deltas.append(
                {
                    "category": category,
                    "baseline_share": round_metric(base_share),
                    "sample_share": round_metric(sample_share),
                    "delta": round_metric(abs(sample_share - base_share)),
                }
            )
        deltas.sort(key=lambda item: (-item["delta"], item["category"]))
        max_delta = max(item["delta"] for item in deltas)
        threshold = float(self.spec.get("threshold"))
        violated = max_delta > threshold + 1e-12
        return self._result(
            violated=violated,
            metric=round_metric(max_delta),
            threshold=threshold,
            evidence={
                "scope": "sample+baseline",
                "column": column,
                "baseline": baseline_ref,
                "metric": "max_absolute_share_delta",
                "sample_size": sample_total,
                "baseline_size": baseline_total,
                "categories": len(categories),
                "top_deltas": deltas[:15],
                **meta,
            },
            message=(
                f"字段 {column} 样本相对基线的最大占比偏移 {max_delta:.6f} 超过阈值 {threshold:.6f}"
                if violated
                else f"字段 {column} 样本相对基线的最大占比偏移 {max_delta:.6f} 未超过 {threshold:.6f}"
            ),
        )
