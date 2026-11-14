"""Field-value duplicate-rate guard."""

from __future__ import annotations

from collections import Counter

from .base import Guard, GuardContext, GuardResult, key_label, round_metric


class DuplicateGuard(Guard):
    type_name = "duplicate"
    code = "GS-GATE-106"

    def evaluate(self, context: GuardContext) -> GuardResult:
        columns = list(self.spec.get("columns", []))
        missing = self._missing(context, columns)
        if missing:
            return self._skip(
                f"重复率字段不存在: {', '.join(missing)}",
                {"scope": "sample", "missing_columns": missing},
            )
        if not context.sample:
            return self._skip(
                "样本为空，无法计算重复率",
                {"scope": "sample", "columns": columns, "sample_size": 0},
            )
        max_rate = float(self.spec.get("max_rate"))
        keys = [tuple(row.data.get(name) for name in columns) for row in context.sample]
        counts = Counter(keys)
        duplicate_excess = len(keys) - len(counts)
        rate = duplicate_excess / len(keys)
        violated = rate > max_rate + 1e-12
        hits = [
            row.record_no
            for row, key in zip(context.sample, keys)
            if counts[key] > 1
        ]
        top = sorted(
            (
                {"key": key_label(key), "count": count}
                for key, count in counts.items()
                if count > 1
            ),
            key=lambda item: (-item["count"], repr(item["key"])),
        )
        return self._result(
            violated=violated,
            metric=round_metric(rate),
            threshold=max_rate,
            evidence={
                "scope": "sample",
                "columns": columns,
                "sample_size": len(keys),
                "distinct_count": len(counts),
                "duplicate_excess": duplicate_excess,
                "duplicate_rate": round_metric(rate),
                "top_duplicates": top[:10],
                "nulls_count_as_values": True,
                **self._rows(hits),
            },
            message=(
                f"字段 {', '.join(columns)} 样本重复率 {rate:.6f} 超过上限 {max_rate:.6f}"
                if violated
                else f"字段 {', '.join(columns)} 样本重复率 {rate:.6f} 在 {max_rate:.6f} 以内"
            ),
        )
