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


@pytest.mark.parametrize(
    ("mutation", "expected_code", "expected_location"),
    [
        # count: unknown key -> GS-CFG-030 at the assertion itself
        (
            lambda contract: contract["assertions"][0].update({"unexpected": 1}),
            "GS-CFG-030",
            "contract.assertions[0]",
        ),
        # unique: undeclared schema column -> GS-CFG-030 at the columns field
        (
            lambda contract: contract["assertions"][1].update({"columns": ["no_such_column"]}),
            "GS-CFG-030",
            "contract.assertions[1].columns",
        ),
        # domain: empty allowed list -> GS-CFG-030 at the allowed field
        (
            lambda contract: contract["assertions"][2].update({"allowed": []}),
            "GS-CFG-030",
            "contract.assertions[2].allowed",
        ),
        # range: no boundary declared -> GS-CFG-030 at the assertion itself
        (
            lambda contract: [
                contract["assertions"][3].pop(key, None)
                for key in ("min", "max", "min_length", "max_length")
            ],
            "GS-CFG-030",
            "contract.assertions[3]",
        ),
        # nullrate: missing required max_rate -> GS-CFG-004 at the field
        (
            lambda contract: contract["assertions"][4].pop("max_rate"),
            "GS-CFG-004",
            "contract.assertions[4].max_rate",
        ),
        # drift: unsupported metric -> GS-CFG-030 at the metric field
        (
            lambda contract: contract["assertions"][6].update({"metric": "psi"}),
            "GS-CFG-030",
            "contract.assertions[6].metric",
        ),
    ],
)
def test_demo_contract_invalid_params_keep_code_and_location(
    config_factory, base_contract, mutation, expected_code, expected_location
):
    """Parameter validation now lives on the guards, but the public error
    contract (code + location) must stay byte-for-byte identical."""
    contract = copy.deepcopy(base_contract)
    mutation(contract)
    path = config_factory(contract=contract)
    with pytest.raises(GateSamplerError) as caught:
        load_run_config(path)
    assert caught.value.code == expected_code
    assert caught.value.location == expected_location


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
