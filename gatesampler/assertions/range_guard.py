"""Numeric-bound, date-bound, and string-length range guard."""

from __future__ import annotations

from datetime import date
from typing import Any

from .base import Guard, GuardContext, GuardResult


def _iso_date_text(value: Any) -> str | None:
    """Return the canonical ISO date string for comparable values, else None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _date_bound(value: Any) -> str:
    """Normalize a declared date bound; contract validation guarantees ISO input."""
    return date.fromisoformat(str(value).strip()).isoformat()


class RangeGuard(Guard):
    type_name = "range"
    code = "GS-GATE-104"

    def evaluate(self, context: GuardContext) -> GuardResult:
        column = self.spec.get("column")
        missing = self._missing(context, [column])
        if missing:
            return self._skip(
                f"值域字段不存在: {column}",
                {"scope": "sample", "missing_columns": missing},
            )
        has_numeric = any(self.spec.get(key) is not None for key in ("min", "max"))
        has_length = any(self.spec.get(key) is not None for key in ("min_length", "max_length"))
        min_inclusive = self.spec.get("min_inclusive")
        max_inclusive = self.spec.get("max_inclusive")
        switches_declared = min_inclusive is not None or max_inclusive is not None
        eff_min_inclusive = True if min_inclusive is None else bool(min_inclusive)
        eff_max_inclusive = True if max_inclusive is None else bool(max_inclusive)
        compare_type = "date" if context.column_types.get(column) == "date" else "number"
        legacy_shape = compare_type == "number" and not switches_declared
        threshold = {
            key: self.spec.get(key)
            for key in (
                "min",
                "max",
                "min_length",
                "max_length",
                "min_inclusive",
                "max_inclusive",
            )
            if self.spec.get(key) is not None
        }
        lowered = self.spec.get("min")
        uppered = self.spec.get("max")
        if compare_type == "date":
            if lowered is not None:
                lowered = _date_bound(lowered)
            if uppered is not None:
                uppered = _date_bound(uppered)
        min_len = self.spec.get("min_length")
        max_len = self.spec.get("max_length")
        checked = 0
        not_comparable = 0
        hits: list[int] = []
        samples: list[dict] = []
        not_comparable_samples: list[dict] = []
        for row in context.sample:
            value = row.data.get(column)
            if value is None:
                continue
            checked += 1
            reasons: list[str] = []
            if has_numeric:
                if compare_type == "date":
                    comparable: Any = _iso_date_text(value)
                elif isinstance(value, bool) or not isinstance(value, (int, float)):
                    comparable = None
                else:
                    comparable = value
                if comparable is None:
                    not_comparable += 1
                    if len(not_comparable_samples) < 10:
                        not_comparable_samples.append(
                            {"record_no": row.record_no, "value": value}
                        )
                else:
                    if lowered is not None and (
                        comparable < lowered
                        or (not eff_min_inclusive and comparable == lowered)
                    ):
                        reasons.append("below_min")
                    if uppered is not None and (
                        comparable > uppered
                        or (not eff_max_inclusive and comparable == uppered)
                    ):
                        reasons.append("above_max")
            if has_length:
                length = len(str(value))
                if min_len is not None and length < min_len:
                    reasons.append("shorter_than_min_length")
                if max_len is not None and length > max_len:
                    reasons.append("longer_than_max_length")
            if reasons:
                hits.append(row.record_no)
                if len(samples) < 10:
                    samples.append(
                        {"record_no": row.record_no, "value": value, "reasons": reasons}
                    )
        if checked == 0:
            return self._skip(
                "字段在样本中没有非空值，无法判定值域",
                {"scope": "sample", "column": column, "checked": 0, "null_excluded": True},
            )
        if has_numeric and not_comparable == checked and not hits:
            return self._skip(
                f"字段 {column} 在样本中没有可参与比较的值",
                {
                    "scope": "sample",
                    "column": column,
                    "checked": checked,
                    "not_comparable": not_comparable,
                    "null_excluded": True,
                },
            )
        violated = bool(hits)
        evidence: dict[str, Any] = {
            "scope": "sample",
            "column": column,
            "checked": checked,
        }
        if not legacy_shape:
            evidence["compare_type"] = compare_type
            evidence["min_inclusive"] = eff_min_inclusive
            evidence["max_inclusive"] = eff_max_inclusive
        if compare_type == "number":
            evidence["non_numeric"] = not_comparable
        evidence["violation_count"] = len(hits)
        evidence["violations"] = samples
        if not_comparable:
            evidence["not_comparable"] = not_comparable
            evidence["not_comparable_samples"] = not_comparable_samples
        evidence["null_excluded"] = True
        evidence.update(self._rows(hits))
        if violated:
            if not_comparable:
                message = (
                    f"字段 {column} 有 {len(hits)} 个样本值越界，"
                    f"另有 {not_comparable} 个值不可比较"
                )
            else:
                message = f"字段 {column} 有 {len(hits)} 个样本值越界或类型不符"
        elif not_comparable:
            message = (
                f"字段 {column} 的 {checked - not_comparable} 个可比较样本值均在值域内，"
                f"{not_comparable} 个值不可比较"
            )
        else:
            message = f"字段 {column} 的 {checked} 个样本值均在值域内"
        return self._result(
            violated=violated,
            metric=len(hits),
            threshold=threshold,
            evidence=evidence,
            message=message,
        )
