from __future__ import annotations

import copy
from typing import Any

import pytest

from gatesampler.assertions import GuardContext, RowRef, run_guards
from gatesampler.config import AssertionSpec, load_run_config
from gatesampler.errors import GateSamplerError


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


def test_drift_metric_defaults_to_legacy_max_share_delta():
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        threshold=0.9,
        baselines={"base.csv": [1, 1, 2, 2]},
        sample=[1, 1, 1],
        column_types={"value": "str"},
    )
    assert result.evidence["metric"] == "max_absolute_share_delta"
    assert result.metric == 0.5
    assert [item["category"] for item in result.evidence["top_deltas"]] == ["1", "2"]


def test_drift_total_variation_reuses_deltas_and_sorts_by_delta():
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="total_variation",
        threshold=0.4,
        baselines={"base.csv": ["a"] * 4 + ["b"] * 4 + ["c"] * 2},
        sample=["a"] * 8 + ["b"] * 2,
        column_types={"value": "str"},
    )
    # shares: a .4->.8 (|.4|), b .4->.2 (|.2|), c .2->.0 (|.2|) => TV = .8/2 = .4
    assert result.evidence["metric"] == "total_variation_distance"
    assert result.metric == 0.4
    top = result.evidence["top_deltas"]
    assert [item["category"] for item in top] == ["a", "b", "c"]
    assert top[0]["delta"] == 0.4
    assert top[1]["delta"] == top[2]["delta"] == 0.2
    # boundary: equal to threshold passes
    assert result.status == "pass"
    violated = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="total_variation",
        threshold=0.39,
        baselines={"base.csv": ["a"] * 4 + ["b"] * 4 + ["c"] * 2},
        sample=["a"] * 8 + ["b"] * 2,
        column_types={"value": "str"},
    )
    assert violated.status == "fail"
    assert violated.metric == 0.4


def test_drift_chi_square_statistic_contribution_order_and_evidence():
    baseline = ["a"] * 40 + ["b"] * 30 + ["c"] * 20 + ["d"] * 10
    sample = ["a"] * 60 + ["b"] * 25 + ["c"] * 10 + ["d"] * 5
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="chi_square",
        threshold=100.0,
        baselines={"base.csv": baseline},
        sample=sample,
        column_types={"value": "str"},
    )
    assert result.evidence["metric"] == "pearson_chi_square"
    assert result.evidence["degrees_of_freedom"] == 3
    assert result.evidence["merged_bin_count"] == 4
    assert result.evidence["min_expected_frequency"] == 5.0
    assert result.evidence["degenerate_binning"] is False
    contributions = [item["chi_square_contribution"] for item in result.evidence["top_deltas"]]
    assert contributions == sorted(contributions, reverse=True)
    assert [item["bins"] for item in result.evidence["top_deltas"]] == [
        "a",
        "c",
        "d",
        "b",
    ]
    assert result.metric == 18.333333
    assert all(
        item["expected_frequency"] >= 5.0 for item in result.evidence["top_deltas"]
    )
    # chi-square contributions sum back to the reported statistic
    assert round(sum(contributions), 6) == result.metric
    violated = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="chi_square",
        threshold=10.0,
        baselines={"base.csv": baseline},
        sample=sample,
        column_types={"value": "str"},
    )
    assert violated.status == "fail"
    assert violated.code == "GS-GATE-107"
    assert violated.metric == 18.333333
    assert "自由度 3" in violated.message


def test_drift_chi_square_merges_low_frequency_bins_with_scaled_expectations():
    # different totals: baseline 100, sample 50 -> expectations scaled to 50
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="chi_square",
        threshold=100.0,
        baselines={"base.csv": ["a"] * 90 + ["b"] * 5 + ["c"] * 5},
        sample=["a"] * 30 + ["b"] * 15 + ["c"] * 5,
        column_types={"value": "str"},
    )
    top = result.evidence["top_deltas"]
    assert result.evidence["merged_bin_count"] == 2
    assert result.evidence["degrees_of_freedom"] == 1
    labels = {item["bins"] for item in top}
    assert labels == {"a", "b+c"}
    merged = next(item for item in top if item["bins"] == "b+c")
    assert merged["baseline_count"] == 10
    assert merged["sample_count"] == 20
    assert merged["expected_frequency"] == 5.0  # 10/100 * 50, scaled to sample total
    assert merged["chi_square_contribution"] == 45.0
    single = next(item for item in top if item["bins"] == "a")
    assert single["expected_frequency"] == 45.0
    assert single["chi_square_contribution"] == 5.0
    assert result.metric == 50.0


def test_drift_chi_square_degenerate_numeric_binning_is_deterministic():
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="chi_square",
        bins=5,
        threshold=1.0,
        baselines={"base.csv": [7, 7, 7, 7]},
        sample=[7, 7, 8],
        column_types={"value": "int"},
    )
    assert result.evidence["degenerate_binning"] is True
    assert "bin_edges" not in result.evidence
    assert result.evidence["merged_bin_count"] == 1
    assert result.evidence["degrees_of_freedom"] == 0
    only = result.evidence["top_deltas"][0]
    assert only["bins"] == "degenerate_bin"
    assert only["baseline_count"] == 4
    assert only["sample_count"] == 3
    assert only["expected_frequency"] == 3.0
    assert only["chi_square_contribution"] == 0.0
    assert result.metric == 0.0
    # share-based metrics keep the legacy per-value degenerate representation
    # and must not carry chi-square-only evidence keys
    legacy = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="max_share_delta",
        bins=5,
        threshold=0.5,
        baselines={"base.csv": [7, 7, 7, 7]},
        sample=[7, 7, 8],
        column_types={"value": "int"},
    )
    assert "degenerate_binning" not in legacy.evidence
    assert "degrees_of_freedom" not in legacy.evidence
    assert {item["category"] for item in legacy.evidence["top_deltas"]} == {"7.0", "8.0"}
    assert legacy.metric == 0.333333


def test_drift_chi_square_numeric_bins_merge_adjacent_empty_cells():
    # wide bin grid with empty interior bins; adjacent low-expected cells fold
    # together in numeric bin order, giving two groups and dof = 1
    result = evaluate(
        "drift",
        column="value",
        baseline="base.csv",
        metric="chi_square",
        bins=5,
        threshold=100.0,
        baselines={
            "base.csv": [1] * 15 + [5] * 6 + [6] * 6 + [7] * 3,
        },
        sample=[1] * 8 + [6] * 2,
        column_types={"value": "int"},
    )
    assert result.evidence["numeric"] is True
    assert result.evidence["merged_bin_count"] == 2
    assert result.evidence["degrees_of_freedom"] == 1
    labels = [item["bins"] for item in result.evidence["top_deltas"]]
    first_indices = [int(label.split("+", 1)[0][4:]) for label in labels]
    assert first_indices == sorted(first_indices)
    assert all(
        item["expected_frequency"] >= 5.0 for item in result.evidence["top_deltas"]
    )


def test_drift_unknown_metric_rejected_at_config_layer(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    drift_assertion = next(item for item in contract["assertions"] if item["type"] == "drift")
    drift_assertion["metric"] = "wasserstein"
    path = config_factory(contract=contract)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-030"
    assert "metric" in (caught.value.location or "")


def test_drift_chi_square_allows_threshold_above_one(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    drift_assertion = next(item for item in contract["assertions"] if item["type"] == "drift")
    drift_assertion["metric"] = "chi_square"
    drift_assertion["threshold"] = 11.34
    path = config_factory(contract=contract)
    loaded = load_run_config(path)
    drift_spec = next(item for item in loaded.contract.assertions if item.type == "drift")
    assert drift_spec.get("metric") == "chi_square"
    assert drift_spec.get("threshold") == 11.34

