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
