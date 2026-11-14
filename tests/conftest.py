from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "fixtures" / "contracts" / "orders_contract.yaml"
BATCH_A = ROOT / "fixtures" / "batches" / "batch_a.csv"
BATCH_B = ROOT / "fixtures" / "batches" / "batch_b.jsonl"
BATCH_BAD = ROOT / "fixtures" / "batches" / "batch_bad.csv"
BASELINE = ROOT / "fixtures" / "batches" / "baseline" / "batch_base.csv"


@pytest.fixture
def base_contract() -> dict:
    data = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    for assertion in data["assertions"]:
        if assertion["type"] == "drift":
            assertion["baseline"] = str(BASELINE)
    return data


@pytest.fixture
def config_factory(tmp_path: Path, base_contract: dict):
    def make(
        contract: dict | None = None,
        batches: list[dict] | None = None,
        *,
        version: str = "1.0",
        strict: bool = False,
        contract_overrides: dict | None = None,
    ) -> Path:
        contract_data = copy.deepcopy(contract if contract is not None else base_contract)
        contract_file = tmp_path / "contract.yaml"
        contract_file.write_text(
            yaml.safe_dump(contract_data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        batch_data = batches or [
            {
                "batch_id": "batch_a",
                "source": str(BATCH_A),
                "format": "csv",
                "expected_rows": 24,
            }
        ]
        config = {
            "version": version,
            "contract": "contract.yaml",
            "strict": strict,
            "output": {"dir": str(tmp_path / "out"), "ledger": str(tmp_path / "out" / "ledger.sqlite3")},
            "batches": batch_data,
        }
        if contract_overrides:
            config["contract_overrides"] = contract_overrides
        config_file = tmp_path / "run.yaml"
        config_file.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return config_file

    return make


@pytest.fixture
def demos(tmp_path: Path) -> dict[str, Path]:
    """A fully local copy of the demo inputs, safe for mutation tests."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures", fixtures)
    configs = tmp_path / "configs"
    configs.mkdir()
    config = (ROOT / "configs" / "demo.yaml").read_text(encoding="utf-8")
    config = config.replace("out/gate_sampler.sqlite3", str(tmp_path / "out" / "ledger.sqlite3"))
    config = config.replace("  dir: out", f"  dir: {tmp_path / 'out'}")
    config_path = configs / "demo.yaml"
    config_path.write_text(config, encoding="utf-8")
    return {"config": config_path, "fixtures": fixtures, "out": tmp_path / "out"}
