from __future__ import annotations

import copy
from pathlib import Path

import pytest
from conftest import ROOT

from gatesampler.config import load_run_config
from gatesampler.errors import GateSamplerError


def assert_code(code: str, callable_) -> None:
    with pytest.raises(GateSamplerError) as caught:
        callable_()
    assert caught.value.code == code


def test_demo_config_and_contract(base_contract):
    config = load_run_config(ROOT / "configs" / "demo.yaml")
    assert config.version == "1.0"
    assert config.contract.gate_id == "orders_gate"
    assert config.contract.sampling.method == "stratified"
    assert config.contract.sampling.seed == 20240117
    assert [batch.batch_id for batch in config.batches] == ["batch_a", "batch_b", "batch_bad"]
    assert all(batch.path.exists() for batch in config.batches)
    assert {item.type for item in config.contract.assertions} == {
        "count",
        "unique",
        "domain",
        "range",
        "nullrate",
        "duplicate",
        "drift",
    }


def test_strict_config_overrides(config_factory):
    config = load_run_config(ROOT / "configs" / "strict.yaml")
    assert config.strict is True
    assert config.contract.sampling.method == "reservoir"
    assert config.contract.sampling.size == 20
    assert config.contract.sampling.strata_field is None
    assert config.out_dir == Path("out-strict")


def test_unsupported_config_version(config_factory):
    path = config_factory(version="2.0")
    assert_code("GS-CFG-002", lambda: load_run_config(path))


def test_unsupported_contract_version(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["contract_version"] = "9.9"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-002", lambda: load_run_config(path))


def test_missing_seed(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    del contract["sampling"]["seed"]
    path = config_factory(contract=contract)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-021"
    assert "seed" in (caught.value.location or "")


def test_unknown_guard_type_and_location(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["assertions"][2]["type"] = "mystery"
    path = config_factory(contract=contract)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-031"
    assert "assertions[2]" in (caught.value.location or "")


def test_duplicate_assertion_id(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["assertions"][1]["id"] = contract["assertions"][0]["id"]
    path = config_factory(contract=contract)
    assert_code("GS-CFG-032", lambda: load_run_config(path))


def test_invalid_range_bound(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["assertions"][3]["min"] = 50
    contract["assertions"][3]["max"] = 10
    path = config_factory(contract=contract)
    assert_code("GS-CFG-030", lambda: load_run_config(path))


def test_undeclared_guard_column(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["assertions"][2]["column"] = "no_such_column"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-030", lambda: load_run_config(path))


def test_missing_contract_yaml(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text('version: "1.0"\ncontract: absent.yaml\nbatches: []\n', encoding="utf-8")
    assert_code("GS-CFG-001", lambda: load_run_config(config))


def test_sampling_size_fraction_exclusive(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["sampling"]["size"] = 10
    contract["sampling"]["fraction"] = 0.5
    path = config_factory(contract=contract)
    assert_code("GS-CFG-020", lambda: load_run_config(path))


def test_strata_field_list_and_per_stratum_min(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["sampling"]["strata_field"] = ["region", "status"]
    contract["sampling"]["per_stratum_min"] = 3
    path = config_factory(contract=contract)
    sampling = load_run_config(path).contract.sampling
    assert sampling.strata_field == ("region", "status")
    assert sampling.strata_fields == ("region", "status")
    assert sampling.per_stratum_min == 3
    assert sampling.to_dict()["strata_field"] == ["region", "status"]


def test_single_strata_field_semantics_unchanged():
    config = load_run_config(ROOT / "configs" / "demo.yaml")
    sampling = config.contract.sampling
    assert sampling.strata_field == "region"
    assert sampling.strata_fields == ("region",)
    assert sampling.per_stratum_min is None
    assert "per_stratum_min" not in sampling.to_dict()


def test_strata_field_list_rejects_unknown_or_duplicate_fields(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["sampling"]["strata_field"] = ["region", "no_such_column"]
    path = config_factory(contract=contract)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-020"
    assert "no_such_column" in caught.value.message

    duplicate = copy.deepcopy(base_contract)
    duplicate["sampling"]["strata_field"] = ["region", "region"]
    path = config_factory(contract=duplicate)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-020"


def test_strata_field_empty_list_rejected(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["sampling"]["strata_field"] = []
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))


def test_per_stratum_min_requires_stratified_and_positive(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    contract["sampling"]["per_stratum_min"] = 0
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))

    reservoir = copy.deepcopy(base_contract)
    reservoir["sampling"] = {"method": "reservoir", "seed": 1, "size": 5, "per_stratum_min": 2}
    path = config_factory(contract=reservoir)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == "GS-CFG-020"
    assert "per_stratum_min" in (caught.value.location or "")
