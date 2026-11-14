"""Field-level sample null-rate guard."""

from __future__ import annotations

from .base import Guard, GuardContext, GuardResult, ParamSpec, round_metric


class NullRateGuard(Guard):
    type_name = "nullrate"
    code = "GS-GATE-105"
    param_schema = (
        ParamSpec("column", "column", required=True),
        ParamSpec("max_rate", "number", required=True, minimum=0.0, maximum=1.0),
    )

    def evaluate(self, context: GuardContext) -> GuardResult:
        column = self.spec.get("column")
        missing = self._missing(context, [column])
        if missing:
            return self._skip(
                f"空值率字段不存在: {column}",
                {"scope": "sample", "missing_columns": missing},
            )
        if not context.sample:
            return self._skip(
                "样本为空，无法计算空值率",
                {"scope": "sample", "column": column, "sample_size": 0},
            )
        max_rate = float(self.spec.get("max_rate"))
        null_rows = [row.record_no for row in context.sample if row.data.get(column) is None]
        rate = len(null_rows) / len(context.sample)
        violated = rate > max_rate + 1e-12
        return self._result(
            violated=violated,
            metric=round_metric(rate),
            threshold=max_rate,
            evidence={
                "scope": "sample",
                "column": column,
                "sample_size": len(context.sample),
                "null_count": len(null_rows),
                "null_rows": null_rows[:20],
                **self._rows(null_rows),
            },
            message=(
                f"字段 {column} 样本空值率 {rate:.6f} 超过上限 {max_rate:.6f}"
                if violated
                else f"字段 {column} 样本空值率 {rate:.6f} 在 {max_rate:.6f} 以内"
            ),
        )
