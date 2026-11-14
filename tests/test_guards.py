from __future__ import annotations

from typing import Any

from gatesampler.assertions import GuardContext, RowRef, run_guards
from gatesampler.config import AssertionSpec


def refs(values: list[Any], key: str = "value") -> list[RowRef]:
    return [RowRef(index + 1, {key: value}) for index, value in enumerate(values)]


def evaluate(
    kind: str,
    *,
    severity: str = "fail",
    sample: list[Any] | None = None,
    full: list[Any] | None = None,
    key: str = "value",
    baselines: dict[str, list[Any]] | None = None,
    column_types: dict[str, str] | None = None,
    batch: dict[str, Any] | None = None,
    **params: Any,
):
    sample_values = [1, 2, 3] if sample is None else sample
    spec = AssertionSpec("check_x", kind, severity, params)
    context = GuardContext(
        sample=refs(sample_values, key),
        full=refs(sample_values if full is None else full, key),
        baselines={
            name: refs(values, key) for name, values in (baselines or {}).items()
        },
        batch=batch or {},
        column_types=column_types or {"value": "int"},
    )
    return run_guards([spec], context)[0]


def test_count_guard_pass_fail_warn_skip_and_code():
    assert evaluate("count", batch={"row_count": 3, "expected_rows": 3}).status == "pass"
    failed = evaluate("count", batch={"row_count": 4, "expected_rows": 3})
    assert (failed.status, failed.code) == ("fail", "GS-GATE-101")
    assert failed.evidence["delta"] == 1
    warn = evaluate("count", severity="warn", batch={"row_count": 5, "expected_rows": 3})
    assert warn.status == "warn"
    skipped = evaluate("count", batch={"row_count": 3})
    assert skipped.status == "skip"
    assert skipped.code == "GS-GATE-101"


def test_unique_guard_sample_and_full_scan():
    passed = evaluate("unique", columns=["value"], full_scan=True)
    assert passed.status == "pass"
    assert passed.evidence["full_scan_reason"]
    failed = evaluate("unique", columns=["value"], full_scan=True, full=[1, 2, 1])
    assert failed.status == "fail"
    assert failed.code == "GS-GATE-102"
    assert failed.evidence["full_duplicate_rows"] == 1
    assert failed.evidence["sample_duplicate_rows"] == 0
    warned = evaluate(
        "unique", severity="warn", columns=["value"], full_scan=True, full=[1, 1]
    )
    assert warned.status == "warn"
    sample_only = evaluate("unique", columns=["value"], sample=[1, 1])
    assert sample_only.status == "fail"
    assert sample_only.evidence["full_scan"] is False
    skipped = evaluate("unique", columns=["missing"])
    assert skipped.status == "skip"


def test_domain_guard_pass_fail_warn_skip():
    assert evaluate("domain", column="value", allowed=[1, 2], sample=[1, 2]).status == "pass"
    failed = evaluate("domain", column="value", allowed=[1, 2], sample=[1, 9])
    assert (failed.status, failed.code) == ("fail", "GS-GATE-103")
    assert failed.evidence["invalid_values"] == [9]
    assert failed.evidence["rows"] == [2]
    assert evaluate("domain", severity="warn", column="value", allowed=[1], sample=[2]).status == "warn"
    assert evaluate("domain", column="value", allowed=[1], sample=[None]).status == "skip"


def test_range_guard_numeric_length_warn_and_skip():
    assert evaluate("range", column="value", min=1, max=3).status == "pass"
    failed = evaluate("range", column="value", min=0, max=2, sample=[-1, 3])
    assert (failed.status, failed.code) == ("fail", "GS-GATE-104")
    assert failed.metric == 2
    assert evaluate("range", column="value", min_length=2, sample=["a", "bb"]).status == "fail"
    assert evaluate("range", severity="warn", column="value", max=1, sample=[2]).status == "warn"
    assert evaluate("range", column="value", min=0, sample=[None]).status == "skip"


def test_range_guard_inclusive_switches_default_closed():
    closed = evaluate("range", column="value", min=1, max=3, sample=[1, 3])
    assert closed.status == "pass"
    assert "min_inclusive" not in closed.evidence
    assert "compare_type" not in closed.evidence
    opened = evaluate("range", column="value", min=1, min_inclusive=False, sample=[1, 2])
    assert opened.status == "fail"
    assert opened.evidence["violations"] == [
        {"record_no": 1, "value": 1, "reasons": ["below_min"]}
    ]
    assert opened.evidence["compare_type"] == "number"
    assert opened.evidence["min_inclusive"] is False
    assert opened.evidence["max_inclusive"] is True
    assert opened.threshold == {"min": 1, "min_inclusive": False}
    opened_max = evaluate("range", column="value", max=3, max_inclusive=False, sample=[2, 3])
    assert opened_max.status == "fail"
    assert opened_max.evidence["violations"][0]["reasons"] == ["above_max"]
    interior = evaluate(
        "range",
        column="value",
        min=1,
        max=3,
        min_inclusive=False,
        max_inclusive=False,
        sample=[2],
    )
    assert interior.status == "pass"


def test_range_guard_date_column_dispatches_iso_comparison():
    passed = evaluate(
        "range",
        column="value",
        min="2024-01-01",
        max="2024-01-31",
        sample=["2024-01-01", "2024-01-31"],
        column_types={"value": "date"},
    )
    assert passed.status == "pass"
    assert passed.evidence["compare_type"] == "date"
    assert passed.evidence["min_inclusive"] is True
    assert passed.evidence["max_inclusive"] is True
    failed = evaluate(
        "range",
        column="value",
        min="2024-01-01",
        max="2024-01-31",
        sample=["2023-12-31", "2024-02-01"],
        column_types={"value": "date"},
    )
    assert failed.status == "fail"
    reasons = [item["reasons"] for item in failed.evidence["violations"]]
    assert reasons == [["below_min"], ["above_max"]]
    opened = evaluate(
        "range",
        column="value",
        min="2024-01-01",
        min_inclusive=False,
        sample=["2024-01-01", "2024-01-02"],
        column_types={"value": "date"},
    )
    assert opened.status == "fail"
    assert opened.evidence["violations"][0]["value"] == "2024-01-01"


def test_range_guard_not_comparable_excluded_from_violations():
    result = evaluate("range", column="value", min=0, max=10, sample=[5, "N/A", None, True])
    assert result.status == "pass"
    assert result.metric == 0
    assert result.evidence["checked"] == 3
    assert result.evidence["violation_count"] == 0
    assert result.evidence["not_comparable"] == 2
    assert result.evidence["non_numeric"] == 2
    assert result.evidence["not_comparable_samples"] == [
        {"record_no": 2, "value": "N/A"},
        {"record_no": 4, "value": True},
    ]
    assert result.evidence["rows"] == []
    assert "不可比较" in result.message


def test_range_guard_not_comparable_with_real_violations():
    result = evaluate("range", column="value", min=0, sample=[-1, "N/A"])
    assert result.status == "fail"
    assert result.metric == 1
    assert result.evidence["violation_count"] == 1
    assert result.evidence["not_comparable"] == 1
    assert result.evidence["violations"][0]["reasons"] == ["below_min"]
    assert "不可比较" in result.message


def test_range_guard_all_values_not_comparable_skips():
    result = evaluate("range", column="value", min=0, sample=["N/A", "void"])
    assert result.status == "skip"
    assert result.evidence["not_comparable"] == 2
    assert result.evidence["checked"] == 2


def test_range_guard_date_column_residue_not_comparable():
    result = evaluate(
        "range",
        column="value",
        min="2024-01-01",
        sample=["2024-01-02", "not-a-date", 20240102],
        column_types={"value": "date"},
    )
    assert result.status == "pass"
    assert result.evidence["compare_type"] == "date"
    assert result.evidence["not_comparable"] == 2
    assert "non_numeric" not in result.evidence
    assert result.evidence["not_comparable_samples"][0]["value"] == "not-a-date"


def test_range_guard_date_min_and_min_length_apply_independently():
    result = evaluate(
        "range",
        column="value",
        min="2024-01-01",
        min_length=10,
        sample=["2024-01-05", "2023-12-31", "2024-01-1"],
        column_types={"value": "date"},
    )
    assert result.status == "fail"
    assert result.threshold["min"] == "2024-01-01"
    assert result.threshold["min_length"] == 10
    assert result.evidence["compare_type"] == "date"
    assert result.evidence["violations"] == [
        {"record_no": 2, "value": "2023-12-31", "reasons": ["below_min"]},
        {"record_no": 3, "value": "2024-01-1", "reasons": ["shorter_than_min_length"]},
    ]
    assert result.evidence["not_comparable"] == 1


def test_nullrate_guard_boundary_fail_warn_skip():
    assert evaluate("nullrate", column="value", max_rate=0.5, sample=[1, None]).status == "pass"
    failed = evaluate("nullrate", column="value", max_rate=0.2, sample=[1, None])
    assert (failed.status, failed.code) == ("fail", "GS-GATE-105")
    assert failed.metric == 0.5
    assert evaluate(
        "nullrate", severity="warn", column="value", max_rate=0.2, sample=[None]
    ).status == "warn"
    assert evaluate("nullrate", column="value", max_rate=0.2, sample=[]).status == "skip"


def test_duplicate_guard_pass_fail_warn_skip():
    assert evaluate("duplicate", columns=["value"], max_rate=0.5, sample=[1, 1]).status == "pass"
    failed = evaluate("duplicate", columns=["value"], max_rate=0.2, sample=[1, 1, 2])
    assert (failed.status, failed.code) == ("fail", "GS-GATE-106")
    assert failed.evidence["duplicate_excess"] == 1
    assert evaluate(
        "duplicate", severity="warn", columns=["value"], max_rate=0.2, sample=[1, 1, 2]
    ).status == "warn"
    assert evaluate("duplicate", columns=["value"], max_rate=0.2, sample=[]).status == "skip"


def test_drift_guard_categorical_numeric_warn_skip():
    baseline = [1, 1, 2, 2]
    passed = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        threshold=0.2,
        baselines={"base.csv": baseline},
        sample=[1, 1, 2],
    )
    assert passed.status == "pass"
    shifted = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        threshold=0.2,
        baselines={"base.csv": baseline},
        sample=[1, 1, 1],
    )
    assert (shifted.status, shifted.code) == ("fail", "GS-GATE-107")
    assert shifted.metric > 0.2
    assert shifted.evidence["top_deltas"]
    warn = evaluate(
        "drift",
        severity="warn",
        column="value",
        baseline="base.csv",
        threshold=0.2,
        baselines={"base.csv": baseline},
        sample=[1, 1, 1],
    )
    assert warn.status == "warn"
    numeric = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        threshold=0.5,
        baselines={"base.csv": list(range(1, 9))},
        sample=list(range(1, 9)),
        column_types={"value": "int"},
    )
    assert numeric.status == "pass"
    skipped = evaluate("drift", column="value", baseline="absent.csv", threshold=0.2)
    assert skipped.status == "skip"
    assert skipped.code == "GS-GATE-107"
