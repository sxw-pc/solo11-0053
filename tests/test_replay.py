from __future__ import annotations

import copy

from gatesampler.config import load_run_config
from gatesampler.ledger import get_judgment, init_db, list_judgments
from gatesampler.replay import replay_judgment
from gatesampler.runner import run_config


def test_replay_matches_original_fixture_judgment(demos):
    config = load_run_config(demos["config"])
    outcomes = run_config(config)
    ledger = config.ledger_path
    connection = init_db(ledger)
    try:
        rows = list_judgments(connection)
    finally:
        connection.close()
    assert len(rows) == 3
    for row in rows:
        replay = replay_judgment(ledger, run_id=row["run_id"])
        assert replay.consistent, replay.mismatches
        assert replay.stored_report_sha256 == replay.recomputed_report_sha256
    review = replay_judgment(ledger, batch_id="batch_b")
    assert review.consistent
    assert review.judgment.decision.outcome == "review"
    assert outcomes[1].judgment.run_id == review.run_id


def test_replay_detects_changed_source(demos):
    config = load_run_config(demos["config"])
    run_config(config)
    source = demos["fixtures"] / "batches" / "batch_a.csv"
    source.write_bytes(source.read_bytes() + b"ord-tampered, cust-x, north, 1.0, paid, 2024-12-31\n")
    result = replay_judgment(config.ledger_path, batch_id="batch_a")
    assert not result.consistent
    assert any(item.startswith("source_sha256:") for item in result.mismatches)


def test_replay_consistent_across_full_scan_toggle(config_factory, base_contract):
    """同一批数据 full_scan 从 true 切到 false：两次判定各自重放都必须一致。"""
    contract = copy.deepcopy(base_contract)
    contract["assertions"][1]["group_by"] = "region"
    contract["assertions"][1]["full_scan"] = True
    config = load_run_config(config_factory(contract=contract))
    run_config(config)
    ledger = config.ledger_path
    contract["assertions"][1]["full_scan"] = False
    config = load_run_config(config_factory(contract=contract))
    run_config(config)
    assert config.ledger_path == ledger
    connection = init_db(ledger)
    try:
        rows = list_judgments(connection)
        assert len(rows) == 2
        stored = [get_judgment(connection, row["run_id"]) for row in rows]
    finally:
        connection.close()
    evidences = []
    for record in stored:
        replay = replay_judgment(ledger, run_id=record["run_id"])
        assert replay.consistent, replay.mismatches
        unique = next(
            item for item in record["assertions"] if item["assertion_id"] == "order_id_unique"
        )
        evidences.append(unique["evidence"])
    by_mode = {item["full_scan"]: item for item in evidences}
    # 样本模式给出组覆盖证据；全量模式为 null，但两种模式的键集合一致
    assert by_mode[False]["group_coverage"]["covered_group_count"] > 0
    assert by_mode[True]["group_coverage"] is None
    assert set(by_mode[True]) == set(by_mode[False])
