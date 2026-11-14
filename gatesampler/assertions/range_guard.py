"""Numeric-bound and string-length range guard."""

from __future__ import annotations

from .base import Guard, GuardContext, GuardResult, ParamRule, ParamSpec


class RangeGuard(Guard):
    type_name = "range"
    code = "GS-GATE-104"
    param_schema = (
        ParamSpec("column", "column", required=True),
        ParamRule(
            "range 至少需要一个边界",
            lambda params: any(
                key in params for key in ("min", "max", "min_length", "max_length")
            ),
        ),
        ParamSpec("min", "number"),
        ParamSpec("max", "number"),
        ParamSpec("min_length", "integer", minimum=0),
        ParamSpec("max_length", "integer", minimum=0),
        ParamRule(
            "min 不得大于 max",
            lambda params: not (
                "min" in params and "max" in params and params["min"] > params["max"]
            ),
        ),
        ParamRule(
            "min_length 不得大于 max_length",
            lambda params: not (
                "min_length" in params
                and "max_length" in params
                and params["min_length"] > params["max_length"]
            ),
        ),
    )

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
        threshold = {
            key: self.spec.get(key)
            for key in ("min", "max", "min_length", "max_length")
            if self.spec.get(key) is not None
        }
        lowered = self.spec.get("min")
        uppered = self.spec.get("max")
        min_len = self.spec.get("min_length")
        max_len = self.spec.get("max_length")
        checked = 0
        bad_type = 0
        hits: list[int] = []
        samples: list[dict] = []
        for row in context.sample:
            value = row.data.get(column)
            if value is None:
                continue
            checked += 1
            reasons: list[str] = []
            if has_numeric:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    bad_type += 1
                    reasons.append("non_numeric")
                else:
                    if lowered is not None and value < lowered:
                        reasons.append("below_min")
                    if uppered is not None and value > uppered:
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
        violated = bool(hits)
        return self._result(
            violated=violated,
            metric=len(hits),
            threshold=threshold,
            evidence={
                "scope": "sample",
                "column": column,
                "checked": checked,
                "non_numeric": bad_type,
                "violation_count": len(hits),
                "violations": samples,
                "null_excluded": True,
                **self._rows(hits),
            },
            message=(
                f"字段 {column} 有 {len(hits)} 个样本值越界或类型不符"
                if violated
                else f"字段 {column} 的 {checked} 个样本值均在值域内"
            ),
        )
