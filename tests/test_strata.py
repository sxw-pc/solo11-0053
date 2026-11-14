from __future__ import annotations

import json

import pytest
from conftest import ROOT

from gatesampler.config import Contract, load_run_config
from gatesampler.config_model import SamplingSpec
from gatesampler.errors import GateSamplerError
from gatesampler.report import render_markdown
from gatesampler.runner import execute_batch, run_config


def _cross_config():
    return load_run_config(ROOT / "configs" / "cross.yaml")


def _limited_contract(config, *, size: int, per_stratum_min: int) -> Contract:
    sampling = SamplingSpec(
        method="stratified",
        seed=config.contract.sampling.seed,
        size=size,
        strata_field=("region", "status"),
        per_stratum_min=per_stratum_min,
    )
    return Contract(
        config.contract.contract_version,
        config.contract.gate_id,
        config.contract.schema,
        sampling,
        config.contract.assertions,
        config.contract.decision,
        config.contract.base_dir,
        config.contract.description,
    )


def test_cross_config_end_to_end_without_undercoverage():
    config = _cross_config()
    judgment = execute_batch(config.contract, config.batches[0])
    sampling = judgment.report["sampling"]
    details = sampling["details"]
    strata = details["strata"]

    assert sampling["size_requested"] == 16
    assert sampling["size_actual"] == 16
    assert details["strata_field"] == ["region", "status"]
    assert details["per_stratum_min"] == 2
    assert details["undercoverage"] is False
    assert details["undercoverage_strata"] == []
    assert details["budget_limited"] is False

    assert sum(row["sample"] for row in strata) == 16
    assert len({tuple(row["sample_indices"]) for row in strata}) == len(strata)
    assert any(row["population"] == 1 for row in strata)
    assert all(row["sample"] <= row["population"] for row in strata)
    assert all(row["sample"] >= row["minimum"] for row in strata)
    assert '["z:null","z:null"]' in {row["stratum"] for row in strata}
    assert len(sampling["sample_record_numbers"]) == 16


def test_cross_config_reports_are_byte_stable_and_markdown_traces_strata():
    config = _cross_config()
    first = execute_batch(config.contract, config.batches[0])
    second = execute_batch(config.contract, config.batches[0])
    assert first.report == second.report
    assert first.sample.sample_fingerprint == second.sample.sample_fingerprint

    markdown = render_markdown(first.report)
    assert "### 分层覆盖" in markdown
    assert "每层最小覆盖：`2`" in markdown
    assert "| 层 | 总体 | 抽样 | 最小覆盖 | 欠覆盖 |" in markdown
    assert '["z:null","z:null"]' in markdown


def test_budget_limited_run_reports_undercoverage_without_overshooting_target():
    config = _cross_config()
    contract = _limited_contract(config, size=11, per_stratum_min=3)
    judgment = execute_batch(contract, config.batches[0])
    sampling = judgment.report["sampling"]
    details = sampling["details"]

    assert sampling["size_actual"] == 11
    assert sampling["size_requested"] == 11
    assert len(judgment.sample.indices) == 11
    assert details["budget_limited"] is True
    assert details["undercoverage"] is True
    assert details["minimum_shortfall"] > 0
    assert sum(row["sample"] for row in details["strata"]) == 11
    assert all(row["sample"] >= 1 for row in details["strata"])
    undercovered = [row["stratum"] for row in details["strata"] if row["undercoverage"]]
    assert undercovered == details["undercoverage_strata"]


def test_cross_config_writes_ledger_and_reports(tmp_path):
    config = _cross_config()
    outcomes = run_config(
        config,
        out_dir=tmp_path / "out",
        ledger_path=tmp_path / "out" / "ledger.sqlite3",
    )
    assert len(outcomes) == 1
    written = json.loads(
        (tmp_path / "out" / "reports" / "batch_cross.json").read_text()
    )
    assert written["sampling"]["details"]["undercoverage"] is False
    assert written["sampling"]["size_actual"] == 16


def test_infeasible_minimum_raises_gs_smp_001():
    config = _cross_config()
    contract = _limited_contract(config, size=2, per_stratum_min=2)
    with pytest.raises(GateSamplerError) as caught:
        execute_batch(contract, config.batches[0])
    assert caught.value.code == "GS-SMP-001"
    assert "无法满足每层最小覆盖" in caught.value.message


def test_cross_contract_parses_from_ledger_snapshot_roundtrip():
    config = _cross_config()
    from gatesampler.config import contract_from_dict

    rebuilt = contract_from_dict(config.contract.to_dict(), config.contract.base_dir)
    assert rebuilt.sampling.strata_field == ("region", "status")
    assert rebuilt.sampling.per_stratum_min == 2
    judgment = execute_batch(rebuilt, config.batches[0])
    original = execute_batch(config.contract, config.batches[0])
    assert judgment.sample.indices == original.sample.indices
    assert judgment.report == original.report
