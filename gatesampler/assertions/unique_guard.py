"""Primary-key uniqueness guard with explicit optional full-batch scan."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..util import stable_scalar
from .base import Guard, GuardContext, GuardResult, key_label, round_metric

_GROUP_LIST_LIMIT = 20


def _normalize_group_by(raw: Any) -> list[str]:
    """Accept a single column name or a list of names; always return a list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


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


def _group_labels(groups) -> list[list]:
    """Render group keys as JSON-safe labels in a deterministic order."""
    ordered = sorted(groups, key=lambda group: tuple(stable_scalar(value) for value in group))
    return [key_label(group) for group in ordered[:_GROUP_LIST_LIMIT]]


def _group_coverage(sample_rows, full_rows, group_by) -> dict[str, Any]:
    """Describe how the sample covers the batch's groups (JSON-safe, stable order)."""
    universe = {tuple(row.data.get(name) for name in group_by) for row in full_rows}
    sampled = {tuple(row.data.get(name) for name in group_by) for row in sample_rows}
    covered = universe & sampled
    uncovered = universe - sampled
    ratio = len(covered) / len(universe) if universe else None
    return {
        "total_groups": len(universe),
        "covered_group_count": len(covered),
        "uncovered_group_count": len(uncovered),
        "covered_ratio": round_metric(ratio),
        "covered_groups": _group_labels(covered),
        "uncovered_groups": _group_labels(uncovered),
        "groups_truncated": (
            len(covered) > _GROUP_LIST_LIMIT or len(uncovered) > _GROUP_LIST_LIMIT
        ),
    }


class UniqueGuard(Guard):
    type_name = "unique"
    code = "GS-GATE-102"

    def evaluate(self, context: GuardContext) -> GuardResult:
        columns = list(self.spec.get("columns", []))
        group_by = _normalize_group_by(self.spec.get("group_by"))
        key_columns = list(dict.fromkeys(group_by + columns))
        missing = self._missing(context, key_columns)
        if missing:
            return self._skip(
                f"唯一性字段不存在: {', '.join(missing)}",
                {
                    "scope": "sample+full",
                    "missing_columns": missing,
                    "group_by": group_by,
                },
            )
        full_scan = bool(self.spec.get("full_scan", False))
        sample_dupes, sample_hits, sample_keys = _duplicates(context.sample, key_columns)
        full_dupes = None
        full_hits: list[int] = []
        full_keys: list[list] = []
        if full_scan:
            full_dupes, full_hits, full_keys = _duplicates(context.full, key_columns)
        coverage = None
        if group_by and not full_scan:
            coverage = _group_coverage(context.sample, context.full, group_by)
        violated = sample_dupes > 0 or bool(full_dupes)
        evidence = {
            "columns": columns,
            "group_by": group_by,
            "scope": "sample+full" if full_scan else "sample",
            "sample_duplicate_rows": sample_dupes,
            "sample_duplicate_keys": sample_keys,
            "sample_rows": self._rows(sample_hits),
            "full_scan": full_scan,
            "full_duplicate_rows": full_dupes,
            "full_duplicate_keys": full_keys,
            "full_rows": self._rows(full_hits),
            "group_coverage": coverage,
            "full_scan_reason": (
                "主键唯一性无法仅由样本证明，契约显式声明 full_scan: true，因此退回全量校验"
                if full_scan
                else "仅做样本内校验，不能证明整批唯一；如需全量须显式声明 full_scan: true"
            ),
        }
        metric = full_dupes if full_scan else sample_dupes
        label = "组内主键" if group_by else "主键"
        if violated:
            message = f"{label}重复: 样本额外重复 {sample_dupes} 行"
            if full_scan:
                message += f"，全量额外重复 {full_dupes} 行"
        else:
            message = f"{label}唯一性检查通过"
            if not full_scan:
                message += "（仅样本范围，未做全量校验）"
            if coverage is not None:
                message += (
                    f"；样本覆盖 {coverage['covered_group_count']}"
                    f"/{coverage['total_groups']} 个分组"
                )
        return self._result(
            violated=violated,
            metric=metric,
            threshold=0,
            evidence=evidence,
            message=message,
        )
