"""Declared-enum domain guard."""

from __future__ import annotations

from .base import Guard, GuardContext, GuardResult, ParamSpec


class DomainGuard(Guard):
    type_name = "domain"
    code = "GS-GATE-103"
    param_schema = (
        ParamSpec("column", "column", required=True),
        ParamSpec("allowed", "scalar_list", required=True, message="allowed 必须是非空标量列表"),
    )

    def evaluate(self, context: GuardContext) -> GuardResult:
        column = self.spec.get("column")
        missing = self._missing(context, [column])
        if missing:
            return self._skip(
                f"枚举字段不存在: {column}",
                {"scope": "sample", "missing_columns": missing},
            )
        allowed = list(self.spec.get("allowed", []))
        allowed_set = set(allowed)
        checked = 0
        hits: list[int] = []
        invalid_values: list = []
        for row in context.sample:
            value = row.data.get(column)
            if value is None:
                continue
            checked += 1
            if value not in allowed_set:
                hits.append(row.record_no)
                if value not in invalid_values:
                    invalid_values.append(value)
        if checked == 0:
            return self._skip(
                "字段在样本中没有非空值，无法判定枚举合法性",
                {"scope": "sample", "column": column, "checked": 0, "null_excluded": True},
            )
        violated = bool(hits)
        return self._result(
            violated=violated,
            metric=len(hits),
            threshold={"allowed": allowed, "invalid_count": 0},
            evidence={
                "scope": "sample",
                "column": column,
                "checked": checked,
                "invalid_count": len(hits),
                "invalid_values": invalid_values[:10],
                "null_excluded": True,
                **self._rows(hits),
            },
            message=(
                f"字段 {column} 有 {len(hits)} 个非法值不在枚举集合内"
                if violated
                else f"字段 {column} 的 {checked} 个样本值均在枚举集合内"
            ),
        )
