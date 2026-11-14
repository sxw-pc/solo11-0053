"""Primary-key uniqueness guard with explicit optional full-batch scan."""

from __future__ import annotations

from collections import Counter

from .base import Guard, GuardContext, GuardResult, key_label


def _duplicates(context_rows, columns) -> tuple[int, list[int], list[list]]:
    counts = Counter(tuple(row.data.get(name) for name in columns) for row in context_rows)
    hits: list[int] = []
    extras = 0
    samples: list[list] = []
    for row in context_rows:
        key = tuple(row.data.get(name) for name in columns)
        if counts[key] > 1:
            hits.append(row.record_no)
            if len(samples) < 10:
                samples.append(key_label(key))
    for key, count in counts.items():
        if count > 1:
            extras += count - 1
    return extras, hits, samples


class UniqueGuard(Guard):
    type_name = "unique"
    code = "GS-GATE-102"

    def evaluate(self, context: GuardContext) -> GuardResult:
        columns = list(self.spec.get("columns", []))
        missing = self._missing(context, columns)
        if missing:
            return self._skip(
                f"唯一性字段不存在: {', '.join(missing)}",
                {"scope": "sample+full", "missing_columns": missing},
            )
        full_scan = bool(self.spec.get("full_scan", False))
        sample_dupes, sample_hits, sample_keys = _duplicates(context.sample, columns)
        full_dupes = None
        full_hits: list[int] = []
        full_keys: list[list] = []
        if full_scan:
            full_dupes, full_hits, full_keys = _duplicates(context.full, columns)
        violated = sample_dupes > 0 or bool(full_dupes)
        evidence = {
            "columns": columns,
            "scope": "sample+full" if full_scan else "sample",
            "sample_duplicate_rows": sample_dupes,
            "sample_duplicate_keys": sample_keys,
            "sample_rows": self._rows(sample_hits),
            "full_scan": full_scan,
            "full_duplicate_rows": full_dupes,
            "full_duplicate_keys": full_keys,
            "full_rows": self._rows(full_hits),
            "full_scan_reason": (
                "主键唯一性无法仅由样本证明，契约显式声明 full_scan: true，因此退回全量校验"
                if full_scan
                else "仅做样本内校验，不能证明整批唯一；如需全量须显式声明 full_scan: true"
            ),
        }
        metric = full_dupes if full_scan else sample_dupes
        if violated:
            message = f"主键重复: 样本额外重复 {sample_dupes} 行"
            if full_scan:
                message += f"，全量额外重复 {full_dupes} 行"
        else:
            message = "主键唯一性检查通过"
            if not full_scan:
                message += "（仅样本范围，未做全量校验）"
        return self._result(
            violated=violated,
            metric=metric,
            threshold=0,
            evidence=evidence,
            message=message,
        )
