from __future__ import annotations

from typing import Any

from gatesampler.assertions import GuardContext, RowRef, run_guards
from gatesampler.config import AssertionSpec
from gatesampler.util import canonical_json


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


def grouped_refs(rows: list[dict]) -> list[RowRef]:
    return [RowRef(index + 1, row) for index, row in enumerate(rows)]


def evaluate_grouped_unique(
    sample_rows: list[dict],
    full_rows: list[dict] | None = None,
    *,
    severity: str = "fail",
    **params: Any,
):
    spec = AssertionSpec("check_x", "unique", severity, params)
    context = GuardContext(
        sample=grouped_refs(sample_rows),
        full=grouped_refs(sample_rows if full_rows is None else full_rows),
        column_types={"order_id": "str", "region": "str", "status": "str"},
    )
    return run_guards([spec], context)[0]


def test_unique_guard_group_by_cross_region_pass_same_group_fail():
    cross_region = [
        {"order_id": "o1", "region": "north"},
        {"order_id": "o1", "region": "south"},
    ]
    passed = evaluate_grouped_unique(
        cross_region, columns=["order_id"], group_by="region", full_scan=True
    )
    assert passed.status == "pass"
    assert passed.evidence["group_by"] == ["region"]
    # 同一输入不加 group_by 时仍是全局重复
    assert (
        evaluate_grouped_unique(cross_region, columns=["order_id"], full_scan=True).status
        == "fail"
    )
    same_group = [
        {"order_id": "o1", "region": "north"},
        {"order_id": "o1", "region": "north"},
    ]
    failed = evaluate_grouped_unique(
        same_group, columns=["order_id"], group_by=["region"], full_scan=True
    )
    assert failed.status == "fail"
    assert failed.code == "GS-GATE-102"
    assert failed.evidence["full_duplicate_rows"] == 1
    # 重复键按命中行逐行记录（与 full_rows 平行），复合键为 [组键, 主键]
    assert failed.evidence["full_duplicate_keys"] == [["north", "o1"], ["north", "o1"]]


def test_unique_guard_group_by_null_region_is_same_group():
    # batch_bad 形态：两条重复 order_id 的 region 均为 None，属同组重复而非跨区误报
    rows = [
        {"order_id": "o1", "region": None},
        {"order_id": "o1", "region": None},
        {"order_id": "o2", "region": "north"},
    ]
    result = evaluate_grouped_unique(
        rows, columns=["order_id"], group_by="region", full_scan=True
    )
    assert result.status == "fail"
    assert result.evidence["full_duplicate_keys"] == [[None, "o1"], [None, "o1"]]
    canonical_json(result.to_dict())  # evidence 必须 JSON 可序列化


def test_unique_guard_group_by_sample_collision_fails():
    sample = [
        {"order_id": "o1", "region": "north"},
        {"order_id": "o1", "region": "north"},
    ]
    full = sample + [{"order_id": "o2", "region": "west"}]
    result = evaluate_grouped_unique(sample, full, columns=["order_id"], group_by="region")
    assert result.status == "fail"
    assert result.evidence["full_scan"] is False
    assert result.evidence["sample_duplicate_rows"] == 1
    assert result.evidence["sample_duplicate_keys"] == [["north", "o1"], ["north", "o1"]]


def test_unique_guard_group_by_sample_coverage_evidence():
    sample = [
        {"order_id": "o1", "region": "north"},
        {"order_id": "o2", "region": "south"},
    ]
    full = sample + [
        {"order_id": "o3", "region": "west"},
        {"order_id": "o4", "region": None},
    ]
    result = evaluate_grouped_unique(sample, full, columns=["order_id"], group_by="region")
    assert result.status == "pass"
    assert "覆盖 2/4 个分组" in result.message
    coverage = result.evidence["group_coverage"]
    assert coverage["total_groups"] == 4
    assert coverage["covered_group_count"] == 2
    assert coverage["uncovered_group_count"] == 2
    assert coverage["covered_ratio"] == 0.5
    assert coverage["covered_groups"] == [["north"], ["south"]]
    # None 组排在字符串组之后（stable_scalar 顺序稳定）
    assert coverage["uncovered_groups"] == [["west"], [None]]
    assert coverage["groups_truncated"] is False
    canonical_json(result.to_dict())
    # full_scan 切换只改取值不改 evidence 键集合，签名仍可比对
    full_result = evaluate_grouped_unique(
        sample, full, columns=["order_id"], group_by="region", full_scan=True
    )
    assert full_result.evidence["group_coverage"] is None
    assert set(full_result.evidence) == set(result.evidence)


def test_unique_guard_group_by_multi_column_and_missing():
    rows = [
        {"order_id": "o1", "region": "north", "status": "paid"},
        {"order_id": "o1", "region": "north", "status": "void"},
    ]
    passed = evaluate_grouped_unique(
        rows, columns=["order_id"], group_by=["region", "status"], full_scan=True
    )
    assert passed.status == "pass"
    assert passed.evidence["group_by"] == ["region", "status"]
    skipped = evaluate_grouped_unique(rows, columns=["order_id"], group_by="absent")
    assert skipped.status == "skip"
    assert skipped.evidence["missing_columns"] == ["absent"]


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
