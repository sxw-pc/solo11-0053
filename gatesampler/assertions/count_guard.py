"""Row-count conservation guard."""

from __future__ import annotations

from .base import Guard, GuardContext, GuardResult


class CountGuard(Guard):
    type_name = "count"
    code = "GS-GATE-101"

    def evaluate(self, context: GuardContext) -> GuardResult:
        expected = self.spec.get("expected")
        if expected is None:
            expected = context.batch.get("expected_rows")
        if expected is None:
            return self._skip(
                "批次未声明 expected_rows，且断言未提供 expected，无法执行行数守恒",
                {"scope": "batch_metadata", "reason": "missing_expected"},
            )
        tolerance = int(self.spec.get("tolerance", 0))
        actual = int(context.batch.get("row_count", len(context.full)))
        delta = actual - int(expected)
        violated = abs(delta) > tolerance
        return self._result(
            violated=violated,
            metric=actual,
            threshold={"expected": int(expected), "tolerance": tolerance},
            evidence={
                "actual": actual,
                "expected": int(expected),
                "delta": delta,
                "tolerance": tolerance,
                "scope": "batch_metadata_and_declared_source_manifest",
            },
            message=(
                f"批次行数 {actual} 与来源声明 {expected} 不符（容差 {tolerance}）"
                if violated
                else f"批次行数 {actual} 与来源声明 {expected} 一致（容差 {tolerance}）"
            ),
        )
