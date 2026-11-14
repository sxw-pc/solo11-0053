from __future__ import annotations

import json

import pytest

from gatesampler.config import SamplingSpec
from gatesampler.errors import GateSamplerError
from gatesampler.sampler import (
    reservoir_indices,
    sample_rows,
    stratified_indices,
    systematic_indices,
    target_size,
)

ROWS = [{"id": index, "region": ["north", "south", "east", "west"][index % 4]} for index in range(40)]

CROSS_ROWS = [
    {"region": "north", "status": "paid"},
    {"region": "north", "status": "paid"},
    {"region": "north", "status": "paid"},
    {"region": "north", "status": "paid"},
    {"region": "north", "status": "refunded"},
    {"region": "north", "status": "refunded"},
    {"region": "north", "status": "refunded"},
    {"region": "south", "status": "paid"},
    {"region": "south", "status": "paid"},
    {"region": "south", "status": "pending"},
    {"region": "east", "status": "cancelled"},
    {"region": "east", "status": "refunded"},
    {"region": "west", "status": "paid"},
    {"region": "west", "status": "paid"},
    {"region": "west", "status": "pending"},
    {"region": "north", "status": "cancelled"},
    {"region": None, "status": None},
]


def test_systematic_is_deterministic_bounded_and_seed_sensitive():
    first = systematic_indices(100, 10, 123)
    second = systematic_indices(100, 10, 123)
    other = systematic_indices(100, 10, 124)
    assert first == second
    assert first != other
    assert len(first) == 10
    assert first == sorted(set(first))
    assert all(0 <= index < 100 for index in first)


def test_systematic_full_and_empty():
    assert systematic_indices(5, 10, 1) == [0, 1, 2, 3, 4]
    assert systematic_indices(0, 3, 1) == []


def test_reservoir_deterministic_and_single_pass_algorithm():
    first = reservoir_indices(1000, 30, 99)
    assert first == reservoir_indices(1000, 30, 99)
    assert first != reservoir_indices(1000, 30, 100)
    assert len(first) == len(set(first)) == 30
    assert first == sorted(first)


def test_stratified_covers_all_strata_and_is_reproducible():
    first, details = stratified_indices(ROWS, 20, 7, "region")
    second, _ = stratified_indices(ROWS, 20, 7, "region")
    assert first == second
    assert len(first) == 20
    assert len(set(first)) == 20
    assert {row["stratum"] for row in details["strata"]} == {
        "s:north",
        "s:south",
        "s:east",
        "s:west",
    }
    assert sum(row["sample"] for row in details["strata"]) == 20
    assert all(row["sample"] >= 1 for row in details["strata"])


def test_stratified_target_smaller_than_strata_fails():
    with pytest.raises(GateSamplerError) as caught:
        stratified_indices(ROWS, 2, 7, "region")
    assert caught.value.code == "GS-SMP-001"


def test_sample_rows_methods_and_fingerprints():
    for method in ("systematic", "stratified", "reservoir"):
        spec = SamplingSpec(
            method=method,
            seed=11,
            size=10,
            strata_field="region" if method == "stratified" else None,
        )
        first = sample_rows(ROWS, spec)
        second = sample_rows(ROWS, spec)
        assert first == second
        assert first.sample_fingerprint == second.sample_fingerprint
        assert first.size_actual == 10
        encoded = json.dumps(first.to_dict(), sort_keys=True)
        assert encoded == json.dumps(second.to_dict(), sort_keys=True)


def test_fraction_and_size_capping():
    spec = SamplingSpec(method="systematic", seed=1, fraction=0.25)
    result = sample_rows(ROWS, spec)
    assert target_size(40, spec) == 10
    assert result.size_actual == 10
    capped = sample_rows(ROWS[:3], SamplingSpec(method="reservoir", seed=1, size=50))
    assert capped.size_actual == 3


def test_cross_strata_are_stable_and_none_safe():
    first, details = stratified_indices(CROSS_ROWS, 16, 7, ["region", "status"])
    keys = {row["stratum"] for row in details["strata"]}
    # Reversing row order must not change stratum identities or the selection.
    reordered, reordered_details = stratified_indices(
        list(reversed(CROSS_ROWS)), 16, 7, ["region", "status"]
    )
    assert keys == {row["stratum"] for row in reordered_details["strata"]}
    assert all(key.startswith('["s:') or key == '["z:null","z:null"]' for key in keys)
    assert '["z:null","z:null"]' in keys
    assert len(first) == len(reordered) == 16
    assert first == stratified_indices(CROSS_ROWS, 16, 7, ["region", "status"])[0]


def test_cross_strata_do_not_collide_across_field_values():
    rows = [
        {"a": "x", "b": "y"},
        {"a": "x|y", "b": ""},
        {"a": "x", "b": None},
        {"a": "", "b": None},
    ]
    _, details = stratified_indices(rows, 4, 1, ["a", "b"])
    keys = [row["stratum"] for row in details["strata"]]
    assert len(keys) == len(set(keys)) == 4
    assert keys == [
        '["s:","z:null"]',
        '["s:x","s:y"]',
        '["s:x","z:null"]',
        '["s:x|y","s:"]',
    ]


def test_per_stratum_min_covers_sparse_strata_then_fills_to_target():
    indices, details = stratified_indices(
        CROSS_ROWS, 16, 9, ["region", "status"], per_stratum_min=3
    )
    assert len(indices) == len(set(indices)) == 16
    assert sum(row["sample"] for row in details["strata"]) == 16
    for row in details["strata"]:
        assert row["minimum"] == min(row["population"], 3)
        assert row["sample"] <= row["population"]
        assert row["sample"] >= row["minimum"]
        assert row["undercoverage"] is False
    assert details["undercoverage"] is False
    assert details["minimum_shortfall"] == 0
    assert details["budget_limited"] is False


def test_per_stratum_min_exceeding_target_shrinks_globally_without_truncation():
    indices, details = stratified_indices(
        CROSS_ROWS, 11, 9, ["region", "status"], per_stratum_min=3
    )
    assert len(indices) == len(set(indices)) == 11
    assert sum(row["sample"] for row in details["strata"]) == 11
    assert details["undercoverage"] is True
    assert details["budget_limited"] is True
    assert details["minimum_shortfall"] == sum(
        max(0, row["minimum"] - row["sample"]) for row in details["strata"]
    )
    undercovered = [row["stratum"] for row in details["strata"] if row["undercoverage"]]
    assert details["undercoverage_strata"] == undercovered
    # Reduction is deterministic for the same input and never empties a stratum.
    assert all(row["sample"] >= 1 for row in details["strata"])
    repeated = stratified_indices(CROSS_ROWS, 11, 9, ["region", "status"], per_stratum_min=3)
    assert repeated == (indices, details)


def test_per_stratum_min_feasible_when_sum_equals_target():
    rows = [{"a": group, "b": str(index)} for group in ("x", "y", "z") for index in range(1)]
    indices, details = stratified_indices(rows, 3, 5, ["a", "b"], per_stratum_min=1)
    assert len(indices) == 3
    assert details["undercoverage"] is False
    assert details["budget_limited"] is False
    assert all(row["sample"] == 1 for row in details["strata"])


def test_target_smaller_than_cross_strata_fails_with_gs_smp_001():
    with pytest.raises(GateSamplerError) as caught:
        stratified_indices(CROSS_ROWS, 2, 9, ["region", "status"], per_stratum_min=2)
    assert caught.value.code == "GS-SMP-001"


def test_sample_rows_supports_cross_fields_and_minimum():
    spec = SamplingSpec(
        method="stratified",
        seed=11,
        size=16,
        strata_field=("region", "status"),
        per_stratum_min=2,
    )
    result = sample_rows(CROSS_ROWS, spec)
    assert result.size_requested == 16
    assert result.size_actual == 16
    assert result.details["strata_field"] == ["region", "status"]
    assert result.details["per_stratum_min"] == 2
    assert result.details["undercoverage"] is False
    assert sample_rows(CROSS_ROWS, spec) == result


def test_single_field_details_keep_legacy_shape():
    _, details = stratified_indices(ROWS, 20, 7, "region")
    assert set(details) == {"strata"}
    assert set(details["strata"][0]) == {"stratum", "population", "sample", "sample_indices"}
