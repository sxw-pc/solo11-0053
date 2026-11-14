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


def _range_spec(contract: dict) -> dict:
    return next(item for item in contract["assertions"] if item["id"] == "amount_bounds")


def _with_date_range(base_contract: dict) -> dict:
    contract = copy.deepcopy(base_contract)
    contract["assertions"].append(
        {
            "id": "created_at_window",
            "type": "range",
            "column": "created_at",
            "min": "2024-01-01",
            "max": "2024-12-31",
        }
    )
    return contract


def test_range_inclusive_switches_accepted(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    _range_spec(contract)["min_inclusive"] = False
    _range_spec(contract)["max_inclusive"] = True
    path = config_factory(contract=contract)
    config = load_run_config(path)
    spec = next(item for item in config.contract.assertions if item.assertion_id == "amount_bounds")
    assert spec.get("min_inclusive") is False
    assert spec.get("max_inclusive") is True


def test_range_inclusive_switch_must_be_boolean(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    _range_spec(contract)["min_inclusive"] = "yes"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))


def test_range_inclusive_switch_rejects_length_mix(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    _range_spec(contract)["max_inclusive"] = False
    _range_spec(contract)["min_length"] = 2
    path = config_factory(contract=contract)
    assert_code("GS-CFG-030", lambda: load_run_config(path))


def test_range_unknown_parameter_still_rejected(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    _range_spec(contract)["min_inclusiv"] = False
    path = config_factory(contract=contract)
    assert_code("GS-CFG-030", lambda: load_run_config(path))


def test_range_date_column_bounds_accepted(config_factory, base_contract):
    path = config_factory(contract=_with_date_range(base_contract))
    config = load_run_config(path)
    spec = next(item for item in config.contract.assertions if item.assertion_id == "created_at_window")
    assert spec.get("min") == "2024-01-01"
    assert spec.get("max") == "2024-12-31"


def test_range_date_column_rejects_numeric_bounds(config_factory, base_contract):
    contract = _with_date_range(base_contract)
    contract["assertions"][-1]["min"] = 0
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))


def test_range_date_column_rejects_bad_date_string(config_factory, base_contract):
    contract = _with_date_range(base_contract)
    contract["assertions"][-1]["max"] = "not-a-date"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))


def test_range_date_column_min_after_max_rejected(config_factory, base_contract):
    contract = _with_date_range(base_contract)
    contract["assertions"][-1]["min"] = "2024-12-31"
    contract["assertions"][-1]["max"] = "2024-01-01"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-030", lambda: load_run_config(path))


def test_range_numeric_column_rejects_string_bounds(config_factory, base_contract):
    contract = copy.deepcopy(base_contract)
    _range_spec(contract)["min"] = "2024-01-01"
    path = config_factory(contract=contract)
    assert_code("GS-CFG-004", lambda: load_run_config(path))


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
