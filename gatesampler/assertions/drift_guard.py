"""First-order distribution drift guard with a pluggable metric family.

Supported metrics:

* ``max_share_delta`` — max absolute category/bin share delta (legacy behavior,
  report bytes are frozen by the existing contract tests).
* ``total_variation`` — total variation distance, i.e. half of the sum of the
  absolute share deltas; reuses the same per-category deltas.
* ``chi_square`` — Pearson chi-square statistic against the baseline. Low-frequency
  bins are merged with their neighbors until the baseline-derived expected
  frequency reaches :data:`MIN_CHI_SQUARE_EXPECTED`. Expected frequencies are
  scaled to the sample total, so the two populations may differ in size.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from .base import Guard, GuardContext, GuardResult, round_metric

#: Baseline-derived expected frequency below which a bin must be merged with a
#: neighboring bin before the chi-square statistic is evaluated.
MIN_CHI_SQUARE_EXPECTED = 5.0

_METRIC_LABELS = {
    "max_share_delta": "max_absolute_share_delta",
    "total_variation": "total_variation_distance",
    "chi_square": "pearson_chi_square",
}


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


def _numeric_sort_key(label: str) -> tuple[int, int | str]:
    if label.startswith("bin_"):
        suffix = label[4:]
        if suffix.isdigit():
            return (0, int(suffix))
    return (1, label)


def _ordered_labels(labels: Sequence[str], numeric: bool) -> list[str]:
    if numeric:
        return sorted(labels, key=_numeric_sort_key)
    return sorted(labels)


def _merge_low_expected(
    labels: Sequence[str],
    baseline_counts: Counter,
    sample_counts: Counter,
    sample_total: int,
    baseline_total: int,
) -> list[dict[str, Any]]:
    """Merge adjacent bins whose baseline-derived expected frequency is too small.

    Bins are processed left to right in their deterministic natural order
    (numeric bin index for binned numeric columns, label order otherwise). A bin
    with expected frequency below :data:`MIN_CHI_SQUARE_EXPECTED` is folded into
    the next bin; the final under-expected bin is folded back into the previous
    one, so the last retained bin always meets the threshold whenever the sample
    total allows it. Expected frequencies are scaled from baseline shares to the
    sample total, supporting different population sizes.
    """
    groups: list[dict[str, Any]] = [
        {
            "labels": [label],
            "baseline_count": baseline_counts.get(label, 0),
            "sample_count": sample_counts.get(label, 0),
        }
        for label in labels
    ]
    merged: list[dict[str, Any]] = []
    for group in groups:
        if merged:
            previous = merged[-1]
            expected = previous["baseline_count"] * sample_total / baseline_total
            if expected < MIN_CHI_SQUARE_EXPECTED:
                previous["labels"].extend(group["labels"])
                previous["baseline_count"] += group["baseline_count"]
                previous["sample_count"] += group["sample_count"]
                continue
        merged.append(group)
    # The final retained group may still be under-expected; fold it backwards.
    if len(merged) > 1:
        last = merged[-1]
        expected = last["baseline_count"] * sample_total / baseline_total
        if expected < MIN_CHI_SQUARE_EXPECTED:
            previous = merged[-2]
            previous["labels"].extend(last["labels"])
            previous["baseline_count"] += last["baseline_count"]
            previous["sample_count"] += last["sample_count"]
            merged.pop()
    return merged


def _chi_square_bins(
    *,
    labels: Sequence[str],
    baseline_counts: Counter,
    sample_counts: Counter,
    sample_total: int,
    baseline_total: int,
    numeric: bool,
) -> tuple[list[dict[str, Any]], float, int]:
    groups = _merge_low_expected(
        labels, baseline_counts, sample_counts, sample_total, baseline_total
    )
    statistic = 0.0
    bins_evidence: list[dict[str, Any]] = []
    for group in groups:
        expected = group["baseline_count"] * sample_total / baseline_total
        observed = group["sample_count"]
        if expected > 0:
            contribution = (observed - expected) ** 2 / expected
        else:  # pragma: no cover - merging guarantees positive baseline counts
            contribution = 0.0
        statistic += contribution
        bins_evidence.append(
            {
                "bins": "+".join(group["labels"]),
                "baseline_count": group["baseline_count"],
                "sample_count": observed,
                "expected_frequency": round_metric(expected),
                "chi_square_contribution": round_metric(contribution),
            }
        )
    degrees_of_freedom = max(0, len(bins_evidence) - 1)

    def tie_key(item: dict[str, Any]) -> tuple[Any, ...]:
        if numeric:
            first = item["bins"].split("+", 1)[0]
            return _numeric_sort_key(first)
        return (1, item["bins"])

    bins_evidence.sort(key=lambda item: (-item["chi_square_contribution"], tie_key(item)))
    return bins_evidence, round_metric(statistic), degrees_of_freedom


class DriftGuard(Guard):
    type_name = "drift"
    code = "GS-GATE-107"

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
        metric_name = self.spec.get("metric", "max_share_delta")
        numeric = context.column_types.get(column) in {"int", "float"}
        bins = int(self.spec.get("bins", 5))
        baseline_values = [row.data.get(column) for row in baseline]
        sample_values = [row.data.get(column) for row in context.sample]
        meta: dict[str, Any] = {"numeric": numeric, "bins": bins if numeric else None}
        degenerate = False
        if numeric:
            usable = [float(value) for value in baseline_values if _numeric(value)]
            if not usable:
                return self._skip(
                    "基线没有可用数值，无法计算数值分箱漂移",
                    {"scope": "sample+baseline", "baseline": baseline_ref, "numeric": True},
                )
            low, high = min(usable), max(usable)
            degenerate = high == low
            if degenerate and metric_name == "chi_square":
                # Degenerate binning (a single baseline value) is collapsed into
                # one deterministic bin instead of producing empty bin_* cells.
                baseline_counts = Counter({"degenerate_bin": len(usable)})
                sample_counts = Counter(
                    {
                        "degenerate_bin": sum(
                            1 for value in sample_values if _numeric(value)
                        )
                    }
                )
            else:
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
        ordered_categories = _ordered_labels(categories, numeric)
        deltas = []
        raw_deltas: dict[str, float] = {}
        for category in ordered_categories:
            base_share = baseline_counts[category] / baseline_total
            sample_share = sample_counts[category] / sample_total
            raw_deltas[category] = abs(sample_share - base_share)
            deltas.append(
                {
                    "category": category,
                    "baseline_share": round_metric(base_share),
                    "sample_share": round_metric(sample_share),
                    "delta": round_metric(abs(sample_share - base_share)),
                }
            )
        threshold = float(self.spec.get("threshold"))
        evidence: dict[str, Any] = {
            "scope": "sample+baseline",
            "column": column,
            "baseline": baseline_ref,
            "metric": _METRIC_LABELS[metric_name],
            "sample_size": sample_total,
            "baseline_size": baseline_total,
            "categories": len(categories),
            **meta,
        }
        if metric_name == "chi_square":
            chi_bins, statistic, degrees_of_freedom = _chi_square_bins(
                labels=ordered_categories,
                baseline_counts=baseline_counts,
                sample_counts=sample_counts,
                sample_total=sample_total,
                baseline_total=baseline_total,
                numeric=numeric,
            )
            evidence.update(
                {
                    "top_deltas": chi_bins[:15],
                    "degrees_of_freedom": degrees_of_freedom,
                    "min_expected_frequency": MIN_CHI_SQUARE_EXPECTED,
                    "merged_bin_count": len(chi_bins),
                    "degenerate_binning": degenerate,
                }
            )
            metric_value = statistic
            violated = metric_value > threshold + 1e-12
            message = (
                f"字段 {column} 样本相对基线的卡方统计量 {metric_value:.6f} "
                f"(自由度 {degrees_of_freedom}) 超过阈值 {threshold:.6f}"
                if violated
                else f"字段 {column} 样本相对基线的卡方统计量 {metric_value:.6f} "
                f"(自由度 {degrees_of_freedom}) 未超过 {threshold:.6f}"
            )
        else:
            deltas.sort(key=lambda item: (-item["delta"], item["category"]))
            evidence["top_deltas"] = deltas[:15]
            if metric_name == "total_variation":
                metric_value = round_metric(sum(raw_deltas.values()) / 2)
                violated = metric_value > threshold + 1e-12
                message = (
                    f"字段 {column} 样本相对基线的总变差距离 {metric_value:.6f} 超过阈值 {threshold:.6f}"
                    if violated
                    else f"字段 {column} 样本相对基线的总变差距离 {metric_value:.6f} 未超过 {threshold:.6f}"
                )
            else:
                metric_value = max(item["delta"] for item in deltas)
                violated = metric_value > threshold + 1e-12
                message = (
                    f"字段 {column} 样本相对基线的最大占比偏移 {metric_value:.6f} 超过阈值 {threshold:.6f}"
                    if violated
                    else f"字段 {column} 样本相对基线的最大占比偏移 {metric_value:.6f} 未超过 {threshold:.6f}"
                )
        return self._result(
            violated=violated,
            metric=metric_value,
            threshold=threshold,
            evidence=evidence,
            message=message,
        )
